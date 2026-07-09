#!/usr/bin/env python3
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


class MockEngineEndToEndTests(unittest.TestCase):
    def test_mock_engine_runs_real_ringer_loop_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            ringer_home = root / "ringer-home"
            state_dir = root / "state"
            workdir = root / "work"
            config_path = root / "config.toml"
            manifest_path = root / "manifest.json"

            home.mkdir()
            ringer_home.mkdir()

            config_path.write_text(
                "\n".join(
                    [
                        f"state_dir = {toml_string(state_dir)}",
                        "",
                        "[eval]",
                        'backend = "jsonl"',
                        f"jsonl_path = {toml_string(root / 'runs.jsonl')}",
                        "",
                        "[artifact]",
                        "enabled = false",
                        "",
                        "[engines.mock]",
                        f"bin = {toml_string(sys.executable)}",
                        "args_template = [",
                        f"  {toml_string(ROOT / 'engines' / 'mock_worker.py')},",
                        '  "{spec}",',
                        "]",
                        "sandbox_args = []",
                        "full_access_args = []",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "mock-engine-test",
                        "workdir": str(workdir),
                        "max_parallel": 2,
                        "worktrees": False,
                        "tasks": [
                            {
                                "key": "hello-task",
                                "engine": "mock",
                                "spec": (
                                    "You are the deterministic mock worker. Write only the file "
                                    "described in this MOCK_FILE block so the executed check can "
                                    "verify the offline worker path.\n"
                                    "MOCK_FILE: hello.txt\n"
                                    "hello from mock\n"
                                    "MOCK_END"
                                ),
                                "check": (
                                    "grep -q hello hello.txt || "
                                    "{ echo FAIL: hello.txt missing hello; exit 1; }"
                                ),
                                "expect_files": ["hello.txt"],
                            },
                            {
                                "key": "fail-task",
                                "engine": "mock",
                                "spec": (
                                    "You are the deterministic mock worker. This task must simulate "
                                    "a worker failure and leave the check without its required file.\n"
                                    "MOCK_FAIL"
                                ),
                                "check": (
                                    "test -f impossible.txt || "
                                    "{ echo FAIL: impossible.txt was not created; exit 1; }"
                                ),
                                "expect_files": ["impossible.txt"],
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["USERPROFILE"] = str(home)
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
                    "mock-test",
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
            self.assertEqual(1, proc.returncode, combined_output)
            self.assertRegex(
                combined_output,
                re.compile(r"^hello-task\s+pass\s+PASS\s+1\s+", re.MULTILINE),
                combined_output,
            )
            self.assertRegex(
                combined_output,
                re.compile(r"^fail-task\s+fail\s+FAIL\s+2\s+", re.MULTILINE),
                combined_output,
            )
            self.assertEqual(
                "hello from mock\n",
                (workdir / "hello-task" / "hello.txt").read_text(encoding="utf-8"),
            )

            fail_log = (workdir / "fail-task" / "worker.log").read_text(encoding="utf-8")
            self.assertIn("mock-worker: simulated failure", fail_log)
            attempt_starts = re.findall(
                r"^\[ringer\.py\] attempt ([12]) started \d{4}-",
                fail_log,
                flags=re.MULTILINE,
            )
            self.assertEqual(["1", "2"], attempt_starts, fail_log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
