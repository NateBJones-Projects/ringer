#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    active_runs_path,
    read_active_runs,
    read_active_runs_file,
    register_active_run,
    scan_hud_run_states,
    unregister_active_run,
)


class ActiveRunsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        os.environ["RINGER_HOME"] = str(Path(self.tmp.name) / "ringer-home")

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_register_read_unregister_round_trip_with_ringer_home_override(self) -> None:
        workdir = Path(self.tmp.name) / "work"
        register_active_run("run-1", "agent-a", "Test Run", workdir)

        runs = read_active_runs()
        self.assertEqual(["run-1"], sorted(runs))
        self.assertEqual(os.getpid(), runs["run-1"]["pid"])
        self.assertEqual("agent-a", runs["run-1"]["identity"])
        self.assertEqual("Test Run", runs["run-1"]["run_name"])
        self.assertEqual(str(workdir), runs["run-1"]["workdir"])
        self.assertTrue(runs["run-1"]["started_at"])
        self.assertEqual(
            (Path(os.environ["RINGER_HOME"]) / "active-runs.json").resolve(),
            active_runs_path(),
        )

        unregister_active_run("run-1")
        self.assertEqual({}, read_active_runs())

    def test_atomic_write_leaves_valid_json_and_no_temp_file(self) -> None:
        register_active_run("run-atomic", "agent-a", "Atomic", Path(self.tmp.name))

        marker = active_runs_path()
        self.assertTrue(marker.exists())
        data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertIn("run-atomic", data)
        self.assertEqual([], list(marker.parent.glob("*.tmp")))
        self.assertEqual([], list(marker.parent.glob(".*.tmp")))

    def test_dead_pid_pruned_on_read_and_write(self) -> None:
        marker = active_runs_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "dead-run": {
                        "pid": 99999999,
                        "identity": "old",
                        "run_name": "Dead",
                        "workdir": str(Path(self.tmp.name) / "dead"),
                        "started_at": "2026-07-05T00:00:00+00:00",
                    }
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual({}, read_active_runs())
        self.assertEqual({}, json.loads(marker.read_text(encoding="utf-8")))

        register_active_run("live-run", "agent-a", "Live", Path(self.tmp.name))
        data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(["live-run"], sorted(data))

    def test_hud_active_reader_prunes_dead_pids(self) -> None:
        marker = active_runs_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "dead-run": {
                        "pid": 99999999,
                        "identity": "old",
                        "run_name": "Dead",
                        "workdir": str(Path(self.tmp.name) / "dead"),
                        "started_at": "2026-07-05T00:00:00+00:00",
                    }
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual({}, read_active_runs_file())
        self.assertEqual({}, json.loads(marker.read_text(encoding="utf-8")))

    def test_hud_projects_orphaned_live_state_as_died(self) -> None:
        state_dir = Path(self.tmp.name) / "state"
        runs_dir = state_dir / "runs"
        runs_dir.mkdir(parents=True)
        state_path = runs_dir / "orphaned-run.json"
        state_path.write_text(
            json.dumps(
                {
                    "run_id": "orphaned-run",
                    "run_name": "Orphaned",
                    "identity": "old",
                    "state": "live",
                    "finished": False,
                    "pid": 99999999,
                }
            ),
            encoding="utf-8",
        )

        runs = scan_hud_run_states(state_dir)
        self.assertEqual(1, len(runs))
        self.assertEqual("died", runs[0]["state"])
        self.assertEqual("live", json.loads(state_path.read_text(encoding="utf-8"))["state"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
