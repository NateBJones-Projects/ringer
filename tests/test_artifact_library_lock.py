#!/usr/bin/env python3
"""Concurrent runs must not erase each other's artifact library entries.

Each test pauses one process between its read and its write of library.json
while a second process updates it. Without a lock held across the
read-modify-write, the paused process writes back a stale copy and the other
process's entry is lost every time.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402

try:
    import fcntl  # noqa: F401
except ImportError:
    HAVE_FCNTL = False
else:
    HAVE_FCNTL = True

# How long the paused process waits for the other one. With the lock, the
# other process is blocked for this long; without it, it finishes in ms.
PAUSE_SECONDS = 2.0


def _pause_after_first_read(paused: Any, resume: Any) -> None:
    original = ringer.read_artifact_library
    state = {"first": True}

    def read_then_pause(state_dir: Path) -> dict[str, Any]:
        library = original(state_dir)
        if state["first"]:
            state["first"] = False
            paused.set()
            resume.wait(PAUSE_SECONDS)
        return library

    ringer.read_artifact_library = read_then_pause


def _worker(
    home: str,
    role: str,
    run_name: str,
    go: Any,
    paused: Any,
    other_started: Any,
    other_done: Any,
    errors: Any,
) -> None:
    os.environ["RINGER_HOME"] = home
    state_dir = Path(home)
    try:
        go.wait(15)
        if role == "paused-live":
            _pause_after_first_read(paused, other_done)
            ringer.update_artifact_library_live(
                state_dir, run_name=run_name, run_id=run_name, identity="a", state="live"
            )
        elif role == "paused-reconcile":
            _pause_after_first_read(paused, other_done)
            ringer.reconcile_artifact_library_dead_runs(state_dir)
        else:
            if not paused.wait(15):
                raise TimeoutError("paused process never read the library")
            other_started.set()
            ringer.append_artifact_library_version(
                state_dir,
                run_name=run_name,
                run_id=run_name,
                identity="b",
                outcome="pass",
                version_path=state_dir / f"{run_name}.html",
                report_path=None,
                tasks_pass=1,
                tasks_fail=0,
            )
            other_done.set()
    except BaseException as exc:
        errors.put(f"{role}: {exc!r}")


@unittest.skipUnless(HAVE_FCNTL, "the library lock uses fcntl, which this platform lacks")
class ArtifactLibraryLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "ringer-home"
        self.home.mkdir()

    def run_pair(self, first: tuple[str, str], second: tuple[str, str]) -> dict[str, Any]:
        ctx = multiprocessing.get_context()
        go, paused, other_started, other_done = (ctx.Event() for _ in range(4))
        errors = ctx.Queue()
        procs = [
            ctx.Process(
                target=_worker,
                args=(str(self.home), role, run_name, go, paused, other_started, other_done, errors),
            )
            for role, run_name in (first, second)
        ]
        for proc in procs:
            proc.start()
        go.set()
        for proc in procs:
            proc.join(30)
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
                self.fail(f"worker {proc.name} hung")
        problems = []
        while not errors.empty():
            problems.append(errors.get())
        self.assertEqual([], problems)
        self.assertTrue(other_started.is_set())
        library = ringer.artifact_library_path(self.home)
        return json.loads(library.read_text(encoding="utf-8"))["artifacts"]

    def test_concurrent_updates_keep_both_runs(self) -> None:
        artifacts = self.run_pair(("paused-live", "run-a"), ("version", "run-b"))
        self.assertEqual(["run-a", "run-b"], sorted(artifacts))

    def test_reconcile_keeps_a_concurrent_update(self) -> None:
        ringer.update_artifact_library_live(
            self.home, run_name="dead-run", run_id="dead-run", identity="x", state="live"
        )
        artifacts = self.run_pair(("paused-reconcile", ""), ("version", "run-b"))
        self.assertEqual(["dead-run", "run-b"], sorted(artifacts))
        self.assertEqual("died", artifacts["dead-run"]["state"])


if __name__ == "__main__":
    unittest.main()
