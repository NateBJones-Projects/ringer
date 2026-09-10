#!/usr/bin/env python3
"""End-to-end: the budget and the circuit breaker must actually fire.

Everything else about these controls was tested by calling helpers and
comparing numbers. That leaves the part most likely to be wrong untested --
an async watcher, a shared counter, worker termination and the run's exit
code -- which is precisely where the first three review passes found defects.

No model is involved. An engine is a binary plus arguments, so a shell that
prints step_finish JSON is a perfectly good worker for this: the runner reads
cost from the log either way.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def q(value: object) -> str:
    return json.dumps(str(value))


class EnforcementEndToEndTests(unittest.TestCase):
    def _config(self, root: Path, worker_sh: str) -> Path:
        path = root / "config.toml"
        path.write_text("\n".join([
            f"state_dir = {q(root / 'state')}",
            "",
            "[eval]",
            'backend = "jsonl"',
            f"jsonl_path = {q(root / 'runs.jsonl')}",
            "",
            "[artifact]",
            "enabled = false",
            "",
            "[engines.spender]",
            f"bin = {q('/bin/sh')}",
            "args_template = [",
            '  "-c",',
            f"  {q(worker_sh)},",
            "]",
            "sandbox_args = []",
            "full_access_args = []",
            "",
        ]), encoding="utf-8")
        return path

    def _run(self, root: Path, manifest: dict, config: Path):
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        env = os.environ.copy()
        env.update(HOME=str(root / "home"), RINGER_HOME=str(root / "rhome"),
                   XDG_CONFIG_HOME=str(root / "xdg"), RINGER_NO_SELF_UPDATE="1")
        (root / "home").mkdir(exist_ok=True)
        (root / "rhome").mkdir(exist_ok=True)
        return subprocess.run(
            [sys.executable, "ringer.py", "run", str(manifest_path),
             "--config", str(config), "--no-dashboard"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)

    def test_a_budget_stops_the_run_and_the_run_reports_failure(self):
        # Each worker announces $5 of spend. A $6 budget must stop the run, and
        # the run must NOT report success -- a budget stop that exits zero would
        # pass a CI gate silently, which is how this became a finding.
        worker = ('printf \'{"type":"step_finish","part":{"cost":5.0,'
                  '"tokens":{"input":1000,"output":10}}}\\n\'; sleep 2; :')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._config(root, worker)
            manifest = {
                "run_name": "budget-e2e", "workdir": str(root / "work"),
                "max_parallel": 1, "worktrees": False, "budget_usd": 6.0,
                "tasks": [
                    {"key": f"t{i}", "engine": "spender", "spec": "spend",
                     "check": "echo checking; exit 0", "verified": "n/a"}
                    for i in range(4)
                ],
            }
            proc = self._run(root, manifest, cfg)
            self.assertIn("BUDGET", proc.stdout.upper() + proc.stderr.upper(),
                          f"no budget stop was reported.\n{proc.stdout[-2000:]}")
            self.assertNotEqual(proc.returncode, 0,
                                "a run stopped by its budget reported success")
            self.assertIn("SKIPPED", proc.stdout + proc.stderr,
                          "queued tasks were not marked SKIPPED")

    def test_identical_failures_stop_the_run_before_the_whole_manifest_is_paid_for(self):
        # Every task fails the same way, as an impossible manifest does. With a
        # limit of 2 the run must stop long before all six have been bought.
        worker = 'printf \'{"type":"step_finish","part":{"cost":0.01}}\\n\'; :'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._config(root, worker)
            manifest = {
                "run_name": "abort-e2e", "workdir": str(root / "work"),
                "max_parallel": 1, "worktrees": False,
                "abort_after_repeated_failures": 2,
                "tasks": [
                    {"key": f"f{i}", "engine": "spender", "spec": "fail",
                     "check": "echo IDENTICAL FAILURE; exit 1",
                     "max_attempts": 1, "verified": "n/a"}
                    for i in range(6)
                ],
            }
            proc = self._run(root, manifest, cfg)
            out = proc.stdout + proc.stderr
            self.assertIn("RUN STOPPED", out, f"the breaker never fired.\n{out[-2000:]}")
            self.assertIn("SKIPPED", out, "later tasks were not skipped after the stop")
            self.assertNotEqual(proc.returncode, 0)

    def test_a_healthy_run_under_budget_still_passes(self):
        # The control must not fire on a run that is behaving. Without this the
        # two tests above would pass against a runner that always stops.
        worker = ('printf \'{"type":"step_finish","part":{"cost":0.001}}\\n\'; '
                  'printf ok > done.txt; :')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._config(root, worker)
            manifest = {
                "run_name": "healthy-e2e", "workdir": str(root / "work"),
                "max_parallel": 2, "worktrees": False, "budget_usd": 5.0,
                "abort_after_repeated_failures": 2,
                "tasks": [
                    {"key": f"ok{i}", "engine": "spender", "spec": "work",
                     "check": "test -s done.txt || { echo FAIL: no done.txt; exit 1; }",
                     "expect_files": ["done.txt"], "verified": "done.txt written"}
                    for i in range(3)
                ],
            }
            proc = self._run(root, manifest, cfg)
            out = proc.stdout + proc.stderr
            self.assertNotIn("RUN STOPPED", out, f"a healthy run was stopped.\n{out[-2000:]}")
            self.assertEqual(proc.returncode, 0, f"healthy run did not pass.\n{out[-2000:]}")


if __name__ == "__main__":
    unittest.main()
