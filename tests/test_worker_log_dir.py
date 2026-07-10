#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    Manifest,
    RingerRunner,
    TaskSpec,
    Verifier,
)


class WorkerLogDirTests(unittest.TestCase):
    """worker_log_dir relocates worker.log (and a standalone check-output sink)
    to a supervisor-owned directory outside the worker-writable taskdir, so the
    worker cannot tamper with the evidence that becomes its own trust record."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "state"
        self.workdir = self.root / "work"
        self.engine = EngineConfig(
            name="mock",
            bin=sys.executable,
            args_template=("-c", "{spec}"),
            full_access_args=(),
            sandbox_args=(),
        )

    def make_config(self, worker_log_dir: Path | None) -> AppConfig:
        artifacts_dir = self.state_dir / "artifacts"
        return AppConfig(
            path=None,
            identity_default=None,
            state_dir=self.state_dir,
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=self.root / "eval.jsonl"),
            engines={"mock": self.engine},
            artifact=ArtifactConfig(
                enabled=True,
                out_template=str(artifacts_dir / "{run_id}.html"),
                report_template=str(artifacts_dir / "{run_id}-report.html"),
                index_out=artifacts_dir / "index.html",
            ),
            worker_log_dir=worker_log_dir,
        )

    def manifest(self, task: TaskSpec) -> Manifest:
        return Manifest.from_obj(
            {
                "run_name": "Rig Run",
                "workdir": str(self.workdir),
                "max_parallel": 1,
                "worktrees": False,
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
        )

    def runner(self, worker_log_dir: Path | None) -> RingerRunner:
        task = TaskSpec(key="t1", spec="noop", check="true", engine="mock")
        return RingerRunner(
            self.manifest(task),
            self.make_config(worker_log_dir),
            "test-agent",
            dashboard_enabled=False,
        )

    def test_worker_log_lands_outside_taskdir_when_configured(self) -> None:
        evidence = self.root / "evidence"
        runner = self.runner(evidence)
        runtime = runner.runtimes[0]
        taskdir = runtime.taskdir
        # The log must not live inside the worker-writable taskdir.
        self.assertNotEqual(taskdir / "worker.log", runtime.log_path)
        self.assertFalse(str(runtime.log_path).startswith(str(taskdir) + os.sep))
        self.assertEqual(evidence.resolve(), runtime.log_path.parent)
        self.assertEqual("t1.worker.log", runtime.log_path.name)

    def test_worker_log_default_stays_in_taskdir(self) -> None:
        runner = self.runner(None)
        runtime = runner.runtimes[0]
        self.assertEqual(runtime.taskdir / "worker.log", runtime.log_path)

    def test_worker_planted_symlink_at_log_path_is_not_written_through(self) -> None:
        evidence = self.root / "evidence"
        secret = self.root / "secret.txt"
        secret.write_text("SECRET\n", encoding="utf-8")
        runner = self.runner(evidence)
        runtime = runner.runtimes[0]
        runtime.taskdir.mkdir(parents=True, exist_ok=True)
        planted = runtime.taskdir / "worker.log"
        planted.symlink_to(secret)
        # The engine writes to the external path, never the planted symlink.
        self.assertNotEqual(planted, runtime.log_path)
        self.assertEqual(evidence.resolve(), runtime.log_path.parent)

    def test_verify_writes_full_untruncated_check_output_sink(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            taskdir = Path(root) / "task"
            taskdir.mkdir()
            sink = Path(root) / "evidence" / "t1.check-output.txt"
            pycode = "import sys; sys.stdout.write('LINE\\n' * 1000)"
            task = TaskSpec(
                key="t1",
                spec="noop",
                check=f"{shlex.quote(sys.executable)} -c {shlex.quote(pycode)}",
                expect_files=(),
            )
            result = asyncio.run(
                Verifier().verify(task, taskdir, check_output_sink=sink)
            )
            self.assertTrue(sink.exists())
            self.assertGreater(len(sink.read_text(encoding="utf-8")), 2000)
            # The in-band excerpt stays bounded; the sink holds the full evidence.
            self.assertLessEqual(len(result.raw_output_excerpt), 2000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
