#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    ARTIFACT_BASE_CSS,
    ARTIFACT_WRAPPER_TAIL_BYTES,
    ArtifactRenderer,
    file_href,
    render_artifact_index_html,
    render_final_report_html,
    render_status_html,
)


class ArtifactWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        self.root = Path(self.tmp.name)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.artifact_path = self.root / "artifacts" / "run-123.html"
        self.renderer = ArtifactRenderer(self.artifact_path)
        self.taskdir = self.root / "work" / "task-one"
        self.taskdir.mkdir(parents=True)
        self.worker_log = self.taskdir / "worker.log"
        self.report_md = self.taskdir / "report.md"
        self.worker_log.write_text("worker log line\nplain <script>log</script>\n", encoding="utf-8")
        self.report_md.write_text("# Report\n<script>alert('x')</script>\n", encoding="utf-8")

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def state(self, *, report_path: Path | None = None) -> dict[str, object]:
        return {
            "run_id": "run-123",
            "run_name": "Wrapper Run",
            "identity": "test-agent",
            "state": "live",
            "started_at": "2026-07-05T00:00:00+00:00",
            "elapsed_s": 1,
            "max_parallel": 1,
            "finished": False,
            "report_ready": report_path is not None,
            "report_path": str(report_path) if report_path else None,
            "totals": {"pass": 0, "fail": 0, "done": 0, "tokens": 0},
            "tasks": [
                {
                    "key": "task-one",
                    "status": "pass",
                    "attempts": 1,
                    "elapsed_s": 1,
                    "check": "echo ok",
                    "spec_short": "write a report",
                    "taskdir": str(self.taskdir),
                    "log_path": str(self.worker_log),
                    "report_paths": {"report.md": str(self.report_md)},
                }
            ],
        }

    def wrapper_path(self, source: Path, *, task_key: str = "task-one", run_id: str = "run-123") -> Path:
        return self.artifact_path.parent / "view" / run_id / f"{task_key}--{source.name}.html"

    def test_status_generates_wrappers_with_escaped_content_and_meta(self) -> None:
        html = render_status_html(self.state(), renderer=self.renderer)

        log_wrapper = self.wrapper_path(self.worker_log)
        report_wrapper = self.wrapper_path(self.report_md)
        self.assertTrue(log_wrapper.exists())
        self.assertTrue(report_wrapper.exists())
        self.assertIn('href="view/', html)
        self.assertIn(log_wrapper.name, html)
        self.assertIn(report_wrapper.name, html)
        self.assertNotIn(file_href(self.worker_log), html)
        self.assertNotIn(file_href(self.report_md), html)

        wrapper_html = log_wrapper.read_text(encoding="utf-8")
        self.assertIn(ARTIFACT_BASE_CSS, wrapper_html)
        self.assertIn("--ground: #0b0e14", wrapper_html)
        self.assertIn(':root[data-theme="dark"]', wrapper_html)
        self.assertIn(':root[data-theme="light"]', wrapper_html)
        self.assertIn("<title>Work log</title>", wrapper_html)
        self.assertIn('<h1 class="briefing">Work log</h1>', wrapper_html)
        self.assertIn('<header class="corner">', wrapper_html)
        self.assertIn('<span class="eyebrow">Ringer &nbsp;·&nbsp; <b>Wrapper Run</b> &nbsp;·&nbsp; task-one</span>', wrapper_html)
        self.assertIn("task-one produced this on <b>", wrapper_html)
        self.assertNotIn(str(self.worker_log), wrapper_html)
        self.assertNotIn(file_href(self.worker_log), wrapper_html)
        self.assertIn("plain &lt;script&gt;log&lt;/script&gt;", wrapper_html)

    def test_final_report_links_wrappers_not_raw_files(self) -> None:
        html = render_final_report_html(self.state(), renderer=self.renderer, force_wrappers=True)

        # Links are RELATIVE (portable over http and file://) — never file:// URLs,
        # which browsers refuse to follow from http-served pages.
        self.assertIn(f'href="view/', html)
        self.assertIn(self.wrapper_path(self.worker_log).name, html)
        self.assertIn(self.wrapper_path(self.report_md).name, html)
        self.assertNotIn(file_href(self.worker_log), html)
        self.assertNotIn(file_href(self.report_md), html)
        self.assertNotIn('href="file://', html)
        self.assertIn(">view the work log</a>", html)
        self.assertIn(">Read what it found</a>", html)
        self.assertNotIn(">worker.log</a>", html)
        self.assertNotIn(">report.md</a>", html)

    def test_large_source_embeds_tail_only_and_says_so(self) -> None:
        large_report = self.taskdir / "large-report.md"
        large_report.write_bytes(
            b"prefix-start\n" + (b"A" * (ARTIFACT_WRAPPER_TAIL_BYTES + 32)) + b"\ntail-end\n"
        )
        state = self.state()
        task = state["tasks"][0]  # type: ignore[index]
        task["report_paths"] = {"report.md": str(large_report)}  # type: ignore[index]

        render_status_html(state, renderer=self.renderer)

        wrapper_html = self.wrapper_path(large_report).read_text(encoding="utf-8")
        self.assertIn(f"Showing the last <b>{ARTIFACT_WRAPPER_TAIL_BYTES:,}</b> bytes", wrapper_html)
        self.assertIn("tail-end", wrapper_html)
        self.assertNotIn("prefix-start", wrapper_html)

    def test_unchanged_source_skips_wrapper_rewrite(self) -> None:
        render_status_html(self.state(), renderer=self.renderer)
        wrapper = self.wrapper_path(self.worker_log)
        old_ns = 1_700_000_000_000_000_000
        os.utime(wrapper, ns=(old_ns, old_ns))

        render_status_html(self.state(), renderer=self.renderer)

        self.assertEqual(old_ns, wrapper.stat().st_mtime_ns)

    def test_report_html_injection_is_escaped_in_wrapper(self) -> None:
        render_status_html(self.state(), renderer=self.renderer)

        wrapper_html = self.wrapper_path(self.report_md).read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;", wrapper_html)
        self.assertNotIn("<script>alert('x')</script>", wrapper_html)

    def test_index_report_entry_wraps_non_html_report_path(self) -> None:
        index_report = self.root / "reports" / "index-report.md"
        index_report.parent.mkdir(parents=True)
        index_report.write_text("index report\n", encoding="utf-8")

        html = render_artifact_index_html(
            [
                {
                    "run_id": "run-abc",
                    "run_name": "Index Run",
                    "identity": "test-agent",
                    "state": "finished",
                    "pass": 1,
                    "fail": 0,
                    "elapsed_s": 2,
                    "artifact_path": str(self.root / "artifacts" / "run-abc.html"),
                    "report_ready": True,
                    "report_path": str(index_report),
                }
            ],
            renderer=self.renderer,
        )

        wrapper = self.wrapper_path(index_report, task_key="run", run_id="run-abc")
        self.assertTrue(wrapper.exists())
        self.assertIn(file_href(wrapper), html)
        self.assertNotIn(file_href(index_report), html)
        self.assertIn(">report</a>", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
