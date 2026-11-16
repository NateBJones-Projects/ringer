#!/usr/bin/env python3
"""Concurrent swarms must not erase each other's active-runs.json entries.

Each test pauses one process between its read and its write of the registry
while a second process updates it. Without a lock held across the
read-modify-write, the paused process writes back a stale copy and the other
process's entry is lost every time.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
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
    original = ringer._read_active_runs_raw
    state = {"first": True}

    def read_then_pause(path: Path) -> dict[str, dict[str, Any]]:
        runs = original(path)
        if state["first"]:
            state["first"] = False
            paused.set()
            resume.wait(PAUSE_SECONDS)
        return runs

    ringer._read_active_runs_raw = read_then_pause


def _worker(
    home: str,
    role: str,
    run_id: str,
    keep_pid: int,
    go: Any,
    paused: Any,
    other_started: Any,
    other_done: Any,
    errors: Any,
) -> None:
    os.environ["RINGER_HOME"] = home
    try:
        go.wait(15)
        if role == "paused-register":
            _pause_after_first_read(paused, other_done)
            ringer.register_active_run(run_id, "a", run_id, Path(home), pid=keep_pid)
        elif role == "paused-read":
            _pause_after_first_read(paused, other_done)
            ringer.read_active_runs()
        else:
            if not paused.wait(15):
                raise TimeoutError("paused process never read the registry")
            other_started.set()
            ringer.register_active_run(run_id, "b", run_id, Path(home), pid=keep_pid)
            other_done.set()
    except BaseException as exc:
        errors.put(f"{role}: {exc!r}")


@unittest.skipUnless(HAVE_FCNTL, "the registry lock uses fcntl, which this platform lacks")
class ActiveRunsLockTests(unittest.TestCase):
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
                args=(str(self.home), role, run_id, os.getpid(), go, paused, other_started, other_done, errors),
            )
            for role, run_id in (first, second)
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
        return json.loads((self.home / "active-runs.json").read_text(encoding="utf-8"))

    def test_concurrent_registers_keep_both_runs(self) -> None:
        runs = self.run_pair(("paused-register", "run-a"), ("register", "run-b"))
        self.assertEqual(["run-a", "run-b"], sorted(runs))

    def test_prune_on_read_keeps_a_concurrent_register(self) -> None:
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()
        (self.home / "active-runs.json").write_text(
            json.dumps({"dead-run": {"pid": dead.pid, "identity": "x", "run_name": "x"}}),
            encoding="utf-8",
        )
        runs = self.run_pair(("paused-read", ""), ("register", "run-b"))
        self.assertEqual(["run-b"], sorted(runs))


if __name__ == "__main__":
    unittest.main()
