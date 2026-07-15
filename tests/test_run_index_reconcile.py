#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402


class RunIndexReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name) / "state"
        self.runs_dir = self.state_dir / "runs"
        self.runs_dir.mkdir(parents=True)

    def write_run(self, run_id: str, data: dict[str, object], mtime: float = 3000.0) -> Path:
        path = self.runs_dir / f"{run_id}.json"
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def dead_pid(self) -> int:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    def entry(self, run_id: str) -> dict[str, object]:
        entries = ringer.scan_run_states(self.state_dir)
        return next(item for item in entries if item["run_id"] == run_id)

    def test_dead_live_run_is_reported_as_died(self) -> None:
        self.write_run("dead-run", {"run_id": "dead-run", "state": "live", "pid": self.dead_pid()})

        entry = self.entry("dead-run")

        self.assertEqual("died", entry["state"])

    def test_live_run_with_alive_pid_stays_live(self) -> None:
        self.write_run("alive-run", {"run_id": "alive-run", "state": "live", "pid": os.getpid()})

        entry = self.entry("alive-run")

        self.assertEqual("live", entry["state"])

    def test_finished_run_is_unaffected(self) -> None:
        self.write_run(
            "finished-run",
            {"run_id": "finished-run", "state": "finished", "finished": True, "pid": self.dead_pid()},
        )

        entry = self.entry("finished-run")

        self.assertEqual("finished", entry["state"])

    def test_live_run_without_usable_pid_stays_live(self) -> None:
        cases = {
            "missing-pid": {"run_id": "missing-pid", "state": "live"},
            "string-pid": {"run_id": "string-pid", "state": "live", "pid": "123"},
        }
        for index, (run_id, data) in enumerate(cases.items()):
            self.write_run(run_id, data, 3000.0 + index)

        entries = {item["run_id"]: item for item in ringer.scan_run_states(self.state_dir)}

        for run_id in cases:
            with self.subTest(run_id=run_id):
                self.assertEqual("live", entries[run_id]["state"])

    def test_scanning_does_not_modify_run_file(self) -> None:
        path = self.write_run(
            "dead-run",
            {"run_id": "dead-run", "state": "live", "pid": self.dead_pid()},
        )
        bytes_before = path.read_bytes()
        mtime_before = path.stat().st_mtime_ns

        entry = self.entry("dead-run")

        self.assertEqual("died", entry["state"])
        self.assertEqual(bytes_before, path.read_bytes())
        self.assertEqual(mtime_before, path.stat().st_mtime_ns)

    def test_dead_run_renders_died_chip_not_live_chip(self) -> None:
        self.write_run("ghost-run", {"run_id": "ghost-run", "state": "live", "pid": self.dead_pid()})

        entries = ringer.scan_run_states(self.state_dir)
        html = ringer.render_artifact_index_html(entries)

        self.assertIn(
            '<span class="chip" style="background:var(--fail)">died</span>',
            html,
        )
        self.assertNotIn(">live</span>", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
