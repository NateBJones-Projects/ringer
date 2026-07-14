#!/usr/bin/env python3
"""One broken engine should cost one attempt, not tasks x attempts.

The startup preflight (preflight_engine_bins) cannot catch every spawn
failure: wrapper-script engines resolve and execute fine, then exit 127
because the binary INSIDE the wrapper is missing. Observed in the field
(2026-07-14): a 16-task run burned 32 doomed spawns that way before
summarizing.

This pins the circuit breaker: after the first INFRA verdict on an engine,
the remaining tasks on that engine fail fast — verdict INFRA, zero attempts,
no worker spawned, no model-log rows, and a worker-log breadcrumb naming the
fix.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def toml_string(value: object) -> str:
    return json.dumps(str(value))


class InfraCircuitBreakerTests(unittest.TestCase):
    def test_first_spawn_failure_trips_breaker_for_remaining_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            ringer_home = root / "ringer-home"
            state_dir = root / "state"
            workdir = root / "work"
            config_path = root / "config.toml"
            manifest_path = root / "manifest.json"
            model_log = root / "runs.jsonl"

            home.mkdir()
            ringer_home.mkdir()

            # A wrapper that exists and is executable (so the startup
            # preflight passes) but whose inner binary is "missing" — the
            # exact field failure shape.
            config_path.write_text(
                "\n".join(
                    [
                        f"state_dir = {toml_string(state_dir)}",
                        "",
                        "[eval]",
                        'backend = "jsonl"',
                        f"jsonl_path = {toml_string(model_log)}",
                        "",
                        "[artifact]",
                        "enabled = false",
                        "",
                        "[engines.broken]",
                        'bin = "/bin/sh"',
                        "args_template = [",
                        '  "-c",',
                        '  "echo wrapper: engine not found on PATH; exit 127",',
                        '  "wrapper",',
                        '  "{spec}",',
                        "]",
                        "sandbox_args = []",
                        "full_access_args = []",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            def task(key: str) -> dict[str, object]:
                return {
                    "key": key,
                    "engine": "broken",
                    "spec": (
                        f"Task {key}: the engine wrapper exits 127 before any "
                        "model is invoked; this spec exists so the manifest "
                        "is well-formed."
                    ),
                    "check": (
                        "test -f never-created.txt || "
                        "{ echo FAIL: worker never ran; exit 1; }"
                    ),
                    "expect_files": ["never-created.txt"],
                }

            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "circuit-breaker-test",
                        "workdir": str(workdir),
                        # Serialized so the breaker deterministically trips
                        # before the queued tasks reach the semaphore.
                        "max_parallel": 1,
                        "worktrees": False,
                        "tasks": [task("spawn-1"), task("spawn-2"), task("spawn-3")],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["RINGER_HOME"] = str(ringer_home)
            env["XDG_CONFIG_HOME"] = str(root / "xdg-config")

            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "run",
                    str(manifest_path),
                    "--config",
                    str(config_path),
                    "--no-dashboard",
                    "--identity",
                    "circuit-breaker-test",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )

            combined_output = proc.stdout + proc.stderr

            # The first task spawned once and was classified INFRA.
            self.assertRegex(
                combined_output,
                re.compile(r"^spawn-1\s+fail\s+INFRA\s+1\s+", re.MULTILINE),
                combined_output,
            )
            # The queued tasks never spawned at all: zero attempts.
            for key in ("spawn-2", "spawn-3"):
                self.assertRegex(
                    combined_output,
                    re.compile(rf"^{key}\s+fail\s+INFRA\s+0\s+", re.MULTILINE),
                    combined_output,
                )

            # No model-log rows for any of them.
            if model_log.exists():
                rows = [
                    json.loads(line)
                    for line in model_log.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.assertEqual([], rows, rows)

            # The first task's log names the spawn failure; the skipped tasks'
            # logs name the breaker and the fix.
            first_log = (workdir / "spawn-1" / "worker.log").read_text(
                encoding="utf-8"
            )
            self.assertIn("engine spawn failure", first_log)
            for key in ("spawn-2", "spawn-3"):
                skipped_log = (workdir / key / "worker.log").read_text(
                    encoding="utf-8"
                )
                self.assertIn("already hit a spawn failure this run", skipped_log)
                self.assertNotIn("attempt 1 exited", skipped_log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
