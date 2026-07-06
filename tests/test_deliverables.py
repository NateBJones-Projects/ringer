#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import posixpath
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    ArtifactRenderer,
    DELIVERABLE_MAX_BYTES,
    EngineConfig,
    EvalConfig,
    Manifest,
    RingerRunner,
    StateWriter,
    TaskRuntime,
    TaskSpec,
    artifact_live_path,
    artifact_version_path,
    read_artifact_library,
    render_final_report_html,
    render_status_html,
)


class WorkLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchor_stack: list[str] = []
        self.href_by_text: dict[str, str] = {}
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "a":
            self.anchor_stack.append(values.get("href", ""))
        elif tag == "img":
            self.images.append(values)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor_stack:
            self.anchor_stack.pop()

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text and self.anchor_stack:
            self.href_by_text[text] = self.anchor_stack[-1]


def resolve_href(page_path: Path, href: str) -> Path:
    parsed = urllib.parse.urlparse(href)
    assert not parsed.scheme, f"expected relative href, got {href!r}"
    joined = posixpath.normpath(
        posixpath.join(page_path.parent.as_posix(), urllib.parse.unquote(parsed.path))
    )
    return Path(joined)


class DeliverableTests(unittest.TestCase):
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
        self.artifacts_dir = self.state_dir / "artifacts"
        self.engine = EngineConfig(
            name="mock",
            bin=sys.executable,
            args_template=("-c", "{spec}"),
            full_access_args=(),
            sandbox_args=(),
        )
        self.artifact = ArtifactConfig(
            enabled=True,
            out_template=str(self.artifacts_dir / "{run_id}.html"),
            report_template=str(self.artifacts_dir / "{run_id}-report.html"),
            index_out=self.artifacts_dir / "index.html",
        )
        self.config = AppConfig(
            path=None,
            identity_default=None,
            state_dir=self.state_dir,
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=self.root / "eval.jsonl"),
            engines={"mock": self.engine},
            artifact=self.artifact,
        )

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def manifest(self, task: TaskSpec, *, worktrees: bool = False) -> Manifest:
        obj: dict[str, object] = {
            "run_name": "Deliverable Run",
            "workdir": str(self.workdir),
            "max_parallel": 1,
            "worktrees": worktrees,
            "tasks": [
                {
                    "key": task.key,
                    "spec": task.spec,
                    "check": task.check,
                    "engine": task.engine,
                    "expect_files": list(task.expect_files),
                }
            ],
        }
        if worktrees:
            repo = self.root / "repo"
            repo.mkdir()
            obj["repo"] = str(repo)
        return Manifest.from_obj(obj)

    def runner(self, task: TaskSpec, *, worktrees: bool = False) -> RingerRunner:
        return RingerRunner(
            self.manifest(task, worktrees=worktrees),
            self.config,
            "test-agent",
            dashboard_enabled=False,
        )

    def runtime_for(self, task: TaskSpec, *, worktrees: bool = False) -> tuple[RingerRunner, TaskRuntime]:
        runner = self.runner(task, worktrees=worktrees)
        runtime = runner.runtimes[0]
        runtime.taskdir.mkdir(parents=True, exist_ok=True)
        runtime.log_path.parent.mkdir(parents=True, exist_ok=True)
        runtime.log_path.write_text("worker done\n", encoding="utf-8")
        return runner, runtime

    def make_large_file(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            fh.seek(DELIVERABLE_MAX_BYTES)
            fh.write(b"x")

    def test_harvest_copies_expected_files_and_records_skips(self) -> None:
        absolute_doc = self.root / "outside" / "market-read.md"
        absolute_doc.parent.mkdir(parents=True)
        absolute_doc.write_text("market read\n", encoding="utf-8")
        home_doc = Path(os.environ["HOME"]) / "home-read.md"
        home_doc.parent.mkdir(parents=True)
        home_doc.write_text("home read\n", encoding="utf-8")
        task = TaskSpec(
            key="task-one",
            spec="Create the requested outputs.",
            check="true",
            engine="mock",
            expect_files=("site-final.html", str(absolute_doc), "~/home-read.md", "missing.txt", "huge.bin"),
        )
        runner, runtime = self.runtime_for(task)
        (runtime.taskdir / "site-final.html").write_text("<h1>done</h1>\n", encoding="utf-8")
        self.make_large_file(runtime.taskdir / "huge.bin")

        runner._harvest_deliverables_on_pass(runtime)

        names = [item["name"] for item in runtime.deliverables]
        self.assertEqual(["site-final.html", "market-read.md", "home-read.md"], names)
        for item in runtime.deliverables:
            copied = Path(str(item["path"]))
            self.assertTrue(copied.exists())
            self.assertEqual(copied.stat().st_size, item["bytes"])
            self.assertEqual(
                self.artifacts_dir / "deliverables" / runner.run_id / "task-one" / item["name"],
                copied,
            )
        self.assertFalse(
            (self.artifacts_dir / "deliverables" / runner.run_id / "task-one" / "missing.txt").exists()
        )
        self.assertFalse(
            (self.artifacts_dir / "deliverables" / runner.run_id / "task-one" / "huge.bin").exists()
        )
        self.assertEqual(1, len(runtime.deliverable_notes))
        self.assertIn("huge.bin", runtime.deliverable_notes[0])
        self.assertIn("20 MB", runtime.deliverable_notes[0])

    def test_harvest_falls_back_to_taskdir_when_no_expect_files(self) -> None:
        task = TaskSpec(
            key="task-one",
            spec="Create the requested outputs.",
            check="true",
            engine="mock",
            expect_files=(),
        )
        runner, runtime = self.runtime_for(task)
        (runtime.taskdir / "review.md").write_text("# Verdict\nfine\n", encoding="utf-8")
        (runtime.taskdir / "site.html").write_text("<h1>hi</h1>\n", encoding="utf-8")
        (runtime.taskdir / ".hidden.md").write_text("nope\n", encoding="utf-8")
        (runtime.taskdir / "junk.tmp").write_text("nope\n", encoding="utf-8")
        (runtime.taskdir / "sub").mkdir()
        (runtime.taskdir / "sub" / "nested.md").write_text("nope\n", encoding="utf-8")

        runner._harvest_deliverables_on_pass(runtime)

        names = sorted(item["name"] for item in runtime.deliverables)
        self.assertEqual(["review.md", "site.html"], names)

    def test_harvest_fallback_skipped_in_worktrees_mode(self) -> None:
        task = TaskSpec(
            key="task-one",
            spec="Create the requested outputs.",
            check="true",
            engine="mock",
            expect_files=(),
        )
        runner, runtime = self.runtime_for(task, worktrees=True)
        # A worktree taskdir root is a repo checkout — repo files must not
        # be mistaken for deliverables.
        (runtime.taskdir / "README.md").write_text("# repo\n", encoding="utf-8")

        runner._harvest_deliverables_on_pass(runtime)

        self.assertEqual([], runtime.deliverables)

    def test_runner_harvests_when_task_passes(self) -> None:
        check_code = "from pathlib import Path; raise SystemExit(0 if Path('site-final.html').is_file() else 1)"
        task = TaskSpec(
            key="task-one",
            spec="from pathlib import Path; Path('site-final.html').write_text('<h1>done</h1>\\n')",
            check=f"{sys.executable!s} -c {json.dumps(check_code)}",
            engine="mock",
            expect_files=("site-final.html",),
        )
        runner = self.runner(task)

        exit_code = asyncio.run(runner.run())

        self.assertEqual(0, exit_code)
        state = json.loads(runner.state_writer.path.read_text(encoding="utf-8"))
        deliverables = state["tasks"][0]["deliverables"]
        self.assertEqual("site-final.html", deliverables[0]["name"])
        self.assertTrue(Path(deliverables[0]["path"]).exists())

    def test_worktrees_mode_harvest_survives_taskdir_removal(self) -> None:
        task = TaskSpec(
            key="task-one",
            spec="Create the requested output.",
            check="true",
            engine="mock",
            expect_files=("site-final.html",),
        )
        runner, runtime = self.runtime_for(task, worktrees=True)
        (runtime.taskdir / "site-final.html").write_text("<h1>done</h1>\n", encoding="utf-8")

        runner._harvest_deliverables_on_pass(runtime)
        shutil.rmtree(runtime.taskdir)

        copied = Path(runtime.deliverables[0]["path"])
        self.assertTrue(copied.exists())
        self.assertEqual("<h1>done</h1>\n", copied.read_text(encoding="utf-8"))

    def harvested_state(self) -> dict[str, object]:
        run_id = "run-123"
        task_key = "task-one"
        root = self.artifacts_dir / "deliverables" / run_id / task_key
        root.mkdir(parents=True)
        site = root / "site-final.html"
        doc = root / "market-read.md"
        image = root / "hero.jpg"
        site.write_text("<h1>site</h1>\n", encoding="utf-8")
        doc.write_text("# Market\n", encoding="utf-8")
        image.write_bytes(
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x01\x00\x01\x00\x00\xff\xd9"
        )
        return {
            "run_id": run_id,
            "run_name": "Deliverable Run",
            "identity": "test-agent",
            "state": "live",
            "started_at": "2026-07-05T00:00:00+00:00",
            "elapsed_s": 12,
            "finished": False,
            "report_ready": False,
            "report_path": None,
            "tasks": [
                {
                    "key": task_key,
                    "status": "pass",
                    "attempts": 1,
                    "elapsed_s": 12,
                    "deliverables": [
                        {"name": site.name, "path": str(site), "bytes": site.stat().st_size},
                        {"name": doc.name, "path": str(doc), "bytes": doc.stat().st_size},
                        {"name": image.name, "path": str(image), "bytes": image.stat().st_size},
                    ],
                }
            ],
        }

    def assert_work_links_resolve(self, html: str, page_path: Path) -> WorkLinkParser:
        parser = WorkLinkParser()
        parser.feed(html)
        self.assertIn("The work", html)
        expected = {
            "Site final — web page": "site-final.html",
            "Market read — document": ".html",
            "Hero — image": "hero.jpg",
        }
        for label, suffix in expected.items():
            href = parser.href_by_text[label]
            resolved = resolve_href(page_path, href)
            self.assertTrue(resolved.exists(), f"{label}: {href} resolved to missing {resolved}")
            if suffix.startswith("."):
                self.assertEqual(suffix, resolved.suffix)
            else:
                self.assertEqual(suffix, resolved.name)
        self.assertNotIn("file://", html)
        return parser

    def test_work_section_relative_links_from_per_run_live_and_version_pages(self) -> None:
        state = self.harvested_state()
        renderer = ArtifactRenderer(self.artifacts_dir / "run-123.html")
        per_run_path = self.artifacts_dir / "run-123.html"
        live_path = artifact_live_path(self.state_dir, "Deliverable Run")
        version_path = artifact_version_path(self.state_dir, "Deliverable Run", "run-123")

        per_run_html = render_status_html(state, renderer=renderer, page_path=per_run_path)
        live_html = render_status_html(state, renderer=renderer, page_path=live_path)
        final_html = render_final_report_html(
            {**state, "state": "finished", "finished": True},
            renderer=renderer,
            page_path=version_path,
        )

        per_run_parser = self.assert_work_links_resolve(per_run_html, per_run_path)
        live_parser = self.assert_work_links_resolve(live_html, live_path)
        final_parser = self.assert_work_links_resolve(final_html, version_path)
        self.assertTrue(per_run_parser.href_by_text["Site final — web page"].startswith("deliverables/"))
        self.assertTrue(live_parser.href_by_text["Site final — web page"].startswith("../deliverables/"))
        self.assertTrue(final_parser.href_by_text["Site final — web page"].startswith("../../deliverables/"))
        wrapper_href = final_parser.href_by_text["Market read — document"]
        wrapper_path = resolve_href(version_path, wrapper_href)
        self.assertIn("/view/run-123/", wrapper_path.as_posix())
        self.assertIn("<title>Market read</title>", wrapper_path.read_text(encoding="utf-8"))
        self.assertTrue(final_parser.images)
        self.assertEqual("work-thumb", final_parser.images[0].get("class"))
        self.assertTrue(final_parser.images[0].get("src", "").startswith("data:image/jpeg;base64,"))

    def test_empty_work_section_and_final_section_order(self) -> None:
        state = {
            "run_id": "run-123",
            "run_name": "Deliverable Run",
            "identity": "test-agent",
            "state": "finished",
            "started_at": "2026-07-05T00:00:00+00:00",
            "elapsed_s": 12,
            "finished": True,
            "tasks": [{"key": "task-one", "status": "pass", "attempts": 1, "elapsed_s": 12}],
        }
        renderer = ArtifactRenderer(self.artifacts_dir / "run-123.html")

        html = render_final_report_html(state, renderer=renderer, page_path=self.artifacts_dir / "run-123.html")

        # A passing task with nothing on the shelf says so on its own group;
        # the page is briefing → rounds → the work, and the old separate
        # timeline/workers sections are gone.
        self.assertIn("Finished and checked — this worker filed nothing to the shelf.", html)
        self.assertLess(html.index('id="what-happened-heading"'), html.index('<div class="rounds"'))
        self.assertLess(html.index('<div class="rounds"'), html.index('id="the-work-heading"'))
        self.assertNotIn('id="status-updates-heading"', html)
        self.assertNotIn('id="tasks-heading"', html)

    def test_library_version_entry_carries_deliverables(self) -> None:
        state = self.harvested_state()
        taskdir = self.workdir / "task-one"
        taskdir.mkdir(parents=True)
        log_path = taskdir / "worker.log"
        log_path.write_text("done\n", encoding="utf-8")
        task = TaskSpec(
            key="task-one",
            spec="Create the requested output.",
            check="true",
            engine="mock",
        )
        runtime = TaskRuntime(
            task=task,
            taskdir=taskdir,
            log_path=log_path,
            status="pass",
            attempts=1,
            deliverables=list(state["tasks"][0]["deliverables"]),  # type: ignore[index]
        )
        runtime.started_at_monotonic = 1.0
        runtime.ended_at_monotonic = 2.0
        writer = StateWriter(
            "run-123",
            "Deliverable Run",
            "test-agent",
            self.state_dir,
            {"mock": self.engine},
            datetime(2026, 7, 5, tzinfo=timezone.utc),
            [runtime],
            threading.RLock(),
            artifact=self.artifact,
        )

        writer.finish()

        entry = read_artifact_library(self.state_dir)["artifacts"]["Deliverable Run"]
        deliverables = entry["versions"][0]["deliverables"]
        self.assertEqual(["site-final.html", "market-read.md", "hero.jpg"], [item["name"] for item in deliverables])
        self.assertEqual("task-one", deliverables[0]["task_key"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
