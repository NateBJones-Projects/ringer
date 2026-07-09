#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    ArtifactConfig,
    ArtifactRenderer,
    EngineConfig,
    StateWriter,
    TaskRuntime,
    TaskSpec,
    render_status_html,
)


class ArtifactEndstateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        self.root = Path(self.tmp.name)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.state_dir = self.root / "state"
        self.workdir = self.root / "work"
        self.renderer = ArtifactRenderer(self.root / "artifacts" / "run.html")

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def task(
        self,
        key: str,
        status: str,
        *,
        attempts: int = 0,
        elapsed_s: float = 0,
        check_output_tail: str = "",
        activity: str | None = None,
    ) -> dict[str, object]:
        task: dict[str, object] = {
            "key": key,
            "status": status,
            "attempts": attempts,
            "elapsed_s": elapsed_s,
            "check_output_tail": check_output_tail,
        }
        if activity is not None:
            task["activity"] = activity
        return task

    def state(self, tasks: list[dict[str, object]], *, finished: bool = False) -> dict[str, object]:
        return {
            "run_id": "run-123",
            "run_name": "Endstate Run",
            "identity": "test-agent",
            "state": "finished" if finished else "live",
            "started_at": "2026-07-05T00:00:00+00:00",
            "elapsed_s": 36,
            "finished": finished,
            "report_ready": False,
            "report_path": None,
            "totals": {"pass": 0, "fail": 0, "done": 0, "running": 0, "tokens": 0},
            "tasks": tasks,
        }

    def runtime(self, status: str = "pass") -> TaskRuntime:
        taskdir = self.workdir / "task-one"
        taskdir.mkdir(parents=True, exist_ok=True)
        log_path = taskdir / "worker.log"
        log_path.write_text("worker finished\n", encoding="utf-8")
        runtime = TaskRuntime(
            task=TaskSpec(
                key="task-one",
                spec="Write the requested file.",
                check="test -s worker.log || { echo missing worker log; exit 1; }",
                engine="mock",
            ),
            taskdir=taskdir,
            log_path=log_path,
            status=status,
            attempts=1,
            spec_short="write file",
        )
        runtime.started_at_monotonic = 1.0
        runtime.ended_at_monotonic = 2.0
        runtime.final_verdict = "PASS" if status == "pass" else "FAIL"
        return runtime

    def writer(self, runtime: TaskRuntime) -> StateWriter:
        engine = EngineConfig(
            name="mock",
            bin=sys.executable,
            args_template=("-c", "pass"),
            full_access_args=(),
            sandbox_args=(),
        )
        artifact = ArtifactConfig(
            enabled=True,
            out_template=str(self.state_dir / "artifacts" / "{run_id}.html"),
            report_template=str(self.state_dir / "artifacts" / "{run_id}-report.html"),
            index_out=self.state_dir / "artifacts" / "index.html",
        )
        return StateWriter(
            "run-123",
            "Endstate Run",
            "test-agent",
            self.state_dir,
            {"mock": engine},
            datetime(2026, 7, 5, tzinfo=timezone.utc),
            [runtime],
            threading.RLock(),
            artifact=artifact,
        )

    def test_finished_writer_rewrites_all_final_pages_without_refresh(self) -> None:
        writer = self.writer(self.runtime(status="pass"))
        writer.finish()

        for path in (writer.artifact_path, writer.live_path, writer.version_path):
            html = path.read_text(encoding="utf-8")
            self.assertNotIn('http-equiv="refresh"', html)
            self.assertIn("Finished ", html)
            self.assertNotIn("this page refreshes itself", html)

    def test_live_render_keeps_refresh(self) -> None:
        html = render_status_html(
            self.state([self.task("task-one", "running", attempts=1)]),
            renderer=self.renderer,
        )

        self.assertIn('<meta http-equiv="refresh" content="2">', html)
        self.assertIn("This page updates itself while the work runs.", html)

    def test_retry_state_shows_on_the_rounds_bar(self) -> None:
        render_status_html(
            self.state([self.task("task-one", "running", attempts=1)]),
            renderer=self.renderer,
        )

        html = render_status_html(
            self.state(
                [
                    self.task(
                        "task-one",
                        "retrying",
                        attempts=2,
                        check_output_tail="\n  FAIL: missing <report>\nsecond line\n",
                    )
                ]
            ),
            renderer=self.renderer,
        )

        self.assertIn('aria-label="task-one: sent back — redoing"', html)
        self.assertIn("Deliverables appear here as workers finish.", html)
        self.assertNotIn("What&#x27;s happening", html)
        self.assertNotIn("What's happening", html)
        self.assertNotIn("The workers", html)

    def test_pass_and_fail_outcomes_show_on_work_groups(self) -> None:
        pass_html = render_status_html(
            self.state([self.task("task-one", "pass", attempts=2, elapsed_s=36)]),
            renderer=self.renderer,
        )
        self.assertIn('<span class="state pass">finished &amp; checked</span>', pass_html)

        other_renderer = ArtifactRenderer(self.root / "artifacts" / "other.html")
        fail_html = render_status_html(
            self.state(
                [
                    self.task(
                        "task-two",
                        "fail",
                        attempts=2,
                        check_output_tail="FAIL: still missing output\n",
                    )
                ],
                finished=True,
            ),
            renderer=other_renderer,
        )
        self.assertIn('<span class="state fail">failed</span>', fail_html)
        # The check's own output is the evidence — one click away, verbatim.
        self.assertIn("See why it failed", fail_html)
        self.assertIn("FAIL: still missing output", fail_html)

    def test_running_task_activity_is_omitted_from_live_work_section(self) -> None:
        html = render_status_html(
            self.state(
                [
                    self.task("task-with-activity", "running", activity="edited report.md"),
                    self.task("task-without-activity", "running"),
                ]
            ),
            renderer=self.renderer,
        )

        self.assertNotIn('<span class="activity" title="edited report.md">', html)
        self.assertIn("Deliverables appear here as workers finish.", html)

    def test_task_rows_include_checklist_glyph_css(self) -> None:
        html = render_status_html(
            self.state(
                [
                    self.task("done", "pass"),
                    self.task("working", "running"),
                    self.task("waiting", "queued"),
                    self.task("failed", "fail"),
                ]
            ),
            renderer=self.renderer,
        )

        self.assertIn(".glyph.pass", html)
        self.assertIn(".glyph.working", html)
        self.assertIn(".glyph.waiting", html)
        self.assertIn(".glyph.retry, .glyph.fail", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
