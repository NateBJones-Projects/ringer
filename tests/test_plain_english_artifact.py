#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import ArtifactRenderer, render_final_report_html, render_status_html  # noqa: E402


class PlainEnglishArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.renderer = ArtifactRenderer(Path(self.tmp.name) / "artifacts" / "run.html")

    def task(
        self,
        key: str,
        status: str,
        *,
        attempts: int = 0,
        elapsed_s: float = 0,
        check_timed_out: bool = False,
    ) -> dict[str, object]:
        return {
            "key": key,
            "status": status,
            "attempts": attempts,
            "elapsed_s": elapsed_s,
            "check": "echo ok && echo rc=1",
            "spec_short": "manifest worktree orchestrator engine spec check",
            "check_timed_out": check_timed_out,
        }

    def state(self, tasks: list[dict[str, object]], *, finished: bool = False, elapsed_s: float = 360) -> dict[str, object]:
        pass_n = sum(1 for task in tasks if task["status"] == "pass")
        fail_n = sum(1 for task in tasks if task["status"] == "fail")
        running_n = sum(1 for task in tasks if task["status"] in {"running", "verifying", "retrying"})
        return {
            "run_id": "run-123",
            "run_name": "Plain English Run",
            "identity": "claude-code-mbp",
            "state": "finished" if finished else "live",
            "started_at": "2026-07-05T00:00:00+00:00",
            "elapsed_s": elapsed_s,
            "max_parallel": 2,
            "finished": finished,
            "report_ready": False,
            "report_path": None,
            "totals": {
                "pass": pass_n,
                "fail": fail_n,
                "done": pass_n + fail_n,
                "running": running_n,
                "tokens": 0,
            },
            "tasks": tasks,
        }

    def test_live_briefing_counts_mixed_state(self) -> None:
        html = render_status_html(
            self.state(
                [
                    self.task("A-pass", "pass", attempts=1),
                    self.task("B-fail", "fail", attempts=2),
                    self.task("C-running", "running", attempts=1),
                    self.task("D-waiting", "queued"),
                ]
            ),
            renderer=self.renderer,
        )

        self.assertIn("Ringer is working on 4 tasks —", html)
        self.assertIn(
            '1 finished and checked</span>, 1 working, 1 is waiting, and <span class="n-fail">1 failed</span>',
            html,
        )

    def test_briefing_is_first_content_after_run_name(self) -> None:
        html = render_status_html(
            self.state([self.task("A-pass", "pass", attempts=1, elapsed_s=5)]),
            renderer=self.renderer,
        )

        self.assertRegex(
            html,
            r'(?s)<div class="page">\s*<header class="corner">.*?'
            r'<span class="eyebrow">Ringer &nbsp;·&nbsp; <b>Plain English Run</b>.*?'
            r'<h1 id="right-now-heading" class="briefing">',
        )

    def test_segmented_bar_has_one_segment_per_task_with_state_class(self) -> None:
        html = render_status_html(
            self.state(
                [
                    self.task("A-pass", "pass"),
                    self.task("B-fail", "fail"),
                    self.task("C-running", "running"),
                    self.task("D-waiting", "queued"),
                ]
            ),
            renderer=self.renderer,
        )

        rounds = re.search(r'<div class="rounds"[^>]*>(.*?)</div>', html, re.S)
        self.assertIsNotNone(rounds)
        self.assertEqual(4, rounds.group(1).count("<span"))
        self.assertEqual(1, rounds.group(1).count('class="pass"'))
        self.assertEqual(1, rounds.group(1).count('class="fail"'))
        self.assertEqual(1, rounds.group(1).count('class="working"'))
        self.assertIn("<span aria-label=\"D-waiting: waiting\"></span>", rounds.group(1))
        self.assertIn("1 finished · 1 working · 1 failed · 1 waiting", html)

    def test_status_change_updates_the_rounds_state_word(self) -> None:
        queued_html = render_status_html(
            self.state([self.task("A-mock-engine", "queued")]), renderer=self.renderer
        )
        self.assertIn('aria-label="A-mock-engine: waiting"', queued_html)

        html = render_status_html(
            self.state([self.task("A-mock-engine", "running", attempts=1)]),
            renderer=self.renderer,
        )
        self.assertIn('aria-label="A-mock-engine: working"', html)

    def test_retry_and_second_try_state_words(self) -> None:
        retry_html = render_status_html(
            self.state([self.task("C-nudge-hooks", "retrying", attempts=1, elapsed_s=20)]),
            renderer=self.renderer,
        )
        self.assertIn('aria-label="C-nudge-hooks: sent back — redoing"', retry_html)

        pass_html = render_status_html(
            self.state([self.task("C-nudge-hooks", "pass", attempts=2, elapsed_s=35)]),
            renderer=self.renderer,
        )
        self.assertIn('<span class="state pass">finished &amp; checked</span>', pass_html)

    def test_full_live_and_final_pages_do_not_use_banned_language(self) -> None:
        tasks = [
            self.task("B-mock-engine", "pass", attempts=1, elapsed_s=330),
            self.task("C-nudge-hooks", "retrying", attempts=1, elapsed_s=22),
        ]
        live_html = render_status_html(
            self.state(
                tasks,
            ),
            renderer=self.renderer,
        )
        final_html = render_final_report_html(self.state(tasks, finished=True), renderer=self.renderer)

        for html in (live_html, final_html):
            scanned = html.lower()
            for task in tasks:
                scanned = scanned.replace(str(task["key"]).lower(), "")
            for banned in ("manifest", "worktree", "orchestrator", "rc=", "exit code", "engine", "spec"):
                self.assertNotIn(banned, scanned)
            self.assertIsNone(re.search(r"\bcheck\b", scanned))

    def test_technical_details_and_raw_table_are_absent(self) -> None:
        live_html = render_status_html(
            self.state([self.task("B-mock-engine", "pass", attempts=1, elapsed_s=330)]),
            renderer=self.renderer,
        )
        final_html = render_final_report_html(
            self.state([self.task("B-mock-engine", "pass", attempts=1, elapsed_s=330)], finished=True),
            renderer=self.renderer,
        )

        for html in (live_html, final_html):
            self.assertNotIn("Technical detail", html)
            self.assertNotIn("Technical detail", html)
            self.assertNotIn("<table", html)

    def test_meta_refresh_is_live_only(self) -> None:
        live_html = render_status_html(
            self.state([self.task("A", "running", attempts=1)]),
            renderer=self.renderer,
        )
        final_html = render_final_report_html(
            self.state([self.task("A", "pass", attempts=1)], finished=True),
            renderer=self.renderer,
        )

        self.assertIn('<meta http-equiv="refresh" content="2">', live_html)
        self.assertNotIn('http-equiv="refresh"', final_html)

    def test_live_work_section_only_shows_finished_tasks(self) -> None:
        logs = Path(self.tmp.name) / "logs"
        logs.mkdir()
        tasks = [
            self.task("finished-task", "pass", attempts=1),
            self.task("running-task", "running", attempts=1),
        ]
        for task in tasks:
            log_path = logs / f'{task["key"]}.log'
            log_path.write_text("work log\n", encoding="utf-8")
            task["log_path"] = str(log_path)

        live_html = render_status_html(self.state(tasks), renderer=self.renderer)
        live_work = re.search(
            r'<section class="work" aria-labelledby="the-work-heading">(.*?)</section>',
            live_html,
            re.S,
        )
        self.assertIsNotNone(live_work)
        self.assertIn('title="finished-task"', live_work.group(1))
        self.assertNotIn('title="running-task"', live_work.group(1))
        self.assertEqual(1, live_work.group(1).count(">view the work log</a>"))

        unfinished_html = render_status_html(
            self.state([tasks[1]]), renderer=self.renderer
        )
        unfinished_work = re.search(
            r'<section class="work" aria-labelledby="the-work-heading">(.*?)</section>',
            unfinished_html,
            re.S,
        )
        self.assertIsNotNone(unfinished_work)
        self.assertIn("Deliverables appear here as workers finish.", unfinished_work.group(1))
        self.assertNotIn('class="work-group"', unfinished_work.group(1))
        self.assertNotIn(">view the work log</a>", unfinished_work.group(1))

        final_html = render_final_report_html(
            self.state(tasks, finished=True), renderer=self.renderer
        )
        self.assertIn('title="finished-task"', final_html)
        self.assertIn('title="running-task"', final_html)
        self.assertEqual(2, final_html.count(">view the work log</a>"))

    def test_theme_override_blocks_are_present(self) -> None:
        html = render_status_html(
            self.state([self.task("A", "running", attempts=1)]),
            renderer=self.renderer,
        )

        self.assertIn(':root[data-theme="dark"]', html)
        self.assertIn(':root[data-theme="light"]', html)

    def test_final_report_all_pass_briefing(self) -> None:
        html = render_final_report_html(
            self.state(
                [
                    self.task("A", "pass", attempts=1),
                    self.task("B", "pass", attempts=1),
                    self.task("C", "pass", attempts=1),
                    self.task("D", "pass", attempts=1),
                ],
                finished=True,
                elapsed_s=330,
            ),
            renderer=self.renderer,
        )

        self.assertIn(
            'Ringer finished 4 tasks in 5m 30s. <span class="n-pass">All 4 finished and checked.</span>',
            html,
        )
        self.assertIn("Finished ", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
