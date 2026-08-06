#!/usr/bin/env python3
"""engines/claude-worker.sh — the Claude Code worker lane wrapper.

Claude Code has no -C/--dir flag, so the wrapper's whole job is to cd into the task directory
(making the agent's cwd the write target) and hand every remaining argument to `claude` unchanged.
These tests prove that with a stub `claude` on PATH, so they run in CI on a machine that has never
installed Claude Code.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "engines" / "claude-worker.sh"

STUB = """#!/bin/sh
# Stub `claude`: record cwd, argv and whether stdin is at EOF, then exit 0.
python3 - "$@" <<'PY'
import json, os, sys
stdin_eof = sys.stdin.read() == ""
with open(os.environ["STUB_OUT"], "w") as fh:
    json.dump({"cwd": os.getcwd(), "argv": sys.argv[1:], "stdin_eof": stdin_eof}, fh)
PY
"""


def _write_stub(bindir: Path, out_path: Path) -> None:
    stub = bindir / "claude"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run(taskdir: Path, args, bindir: Path, out_path: Path):
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["STUB_OUT"] = str(out_path)
    return subprocess.run(
        [str(WRAPPER), str(taskdir), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


class ClaudeWrapperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.taskdir = base / "task"
        self.taskdir.mkdir()
        self.bindir = base / "bin"
        self.bindir.mkdir()
        self.out = base / "stub.json"
        _write_stub(self.bindir, self.out)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_wrapper_is_executable(self) -> None:
        self.assertTrue(WRAPPER.exists(), f"{WRAPPER} is missing")
        self.assertTrue(os.access(WRAPPER, os.X_OK), f"{WRAPPER} is not executable")

    def test_runs_claude_inside_the_task_directory(self) -> None:
        proc = _run(self.taskdir, ["-p", "--model", "sonnet", "do the thing"], self.bindir, self.out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rec = json.loads(self.out.read_text())
        self.assertEqual(
            os.path.realpath(rec["cwd"]),
            os.path.realpath(self.taskdir),
            "the agent's cwd must be the task dir — that is what makes it the write target",
        )

    def test_passes_every_argument_through_unchanged(self) -> None:
        args = ["-p", "--dangerously-skip-permissions", "--model", "opus", "spec with spaces"]
        _run(self.taskdir, args, self.bindir, self.out)
        self.assertEqual(json.loads(self.out.read_text())["argv"], args)

    def test_closes_stdin(self) -> None:
        # A worker that inherits an open stdin can hang a swarm waiting for input.
        _run(self.taskdir, ["-p", "hello"], self.bindir, self.out)
        self.assertTrue(json.loads(self.out.read_text())["stdin_eof"], "stdin must be closed")

    def test_propagates_the_agent_exit_code(self) -> None:
        failing = self.bindir / "claude"
        failing.write_text("#!/bin/sh\nexit 3\n")
        failing.chmod(failing.stat().st_mode | stat.S_IXUSR)
        proc = _run(self.taskdir, ["-p", "x"], self.bindir, self.out)
        self.assertEqual(proc.returncode, 3, "a failed worker must not look like a pass")

    def test_missing_task_directory_fails_loudly(self) -> None:
        proc = _run(Path(self.tmp.name) / "nope", ["-p", "x"], self.bindir, self.out)
        self.assertNotEqual(proc.returncode, 0, "an unusable task dir must fail, not run in the wrong cwd")

    def test_requires_a_task_directory_argument(self) -> None:
        env = dict(os.environ)
        env["PATH"] = f"{self.bindir}{os.pathsep}{env.get('PATH', '')}"
        env["STUB_OUT"] = str(self.out)
        proc = subprocess.run([str(WRAPPER)], capture_output=True, text=True, env=env, timeout=60)
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
