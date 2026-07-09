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


class VanishedTaskdirEndToEndTests(unittest.TestCase):
    def test_worker_destroying_its_worktree_fails_that_lane_only(self) -> None:
        # 2026-07-09 regression: a worker deleted its own worktree, verify's
        # check spawn raised ENOENT, and the unhandled error exited the whole
        # run with code 2 — killing every healthy lane. The run must instead
        # record that lane as FAIL (one attempt — a retry could only ENOENT
        # again) and let siblings finish. Worktrees mode is the topology that
        # crashed: logs live outside the taskdir, so nothing recreates it
        # before verification (non-worktrees mode self-heals via append_text).
        with tempfile.TemporaryDirectory() as temp_root:
            # resolve(): ringer resolves taskdirs, so the worker's $PWD is the
            # /private/var/... canonical form — RINGER_TEST_ROOT must match it.
            root = Path(temp_root).resolve()
            home = root / "home"
            ringer_home = root / "ringer-home"
            state_dir = root / "state"
            workdir = root / "work"
            repo = root / "repo"
            eval_log = root / "runs.jsonl"
            config_path = root / "config.toml"
            manifest_path = root / "manifest.json"

            home.mkdir()
            ringer_home.mkdir()

            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.email=test@example.invalid",
                    "-c",
                    "user.name=ringer-test",
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    "init",
                ],
                check=True,
            )

            config_path.write_text(
                "\n".join(
                    [
                        f"state_dir = {toml_string(state_dir)}",
                        "",
                        "[eval]",
                        'backend = "jsonl"',
                        f"jsonl_path = {toml_string(eval_log)}",
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
                        "[engines.destroyer]",
                        'bin = "/bin/bash"',
                        "args_template = [",
                        '  "-c",',
                        # Refuse to delete anything outside the test sandbox.
                        '  "case \\"$PWD\\" in \\"$RINGER_TEST_ROOT\\"*) rm -rf -- \\"$PWD\\";; *) echo refusing to delete $PWD; exit 3;; esac",',
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
                        "run_name": "vanished-taskdir-test",
                        "workdir": str(workdir),
                        "max_parallel": 2,
                        "worktrees": True,
                        "repo": str(repo),
                        "tasks": [
                            {
                                "key": "destroy-task",
                                "engine": "destroyer",
                                "spec": (
                                    "You simulate the 2026-07-09 failure: a worker that deletes "
                                    "its own task worktree before verification can run, so the "
                                    "harness must record this lane as FAIL without crashing."
                                ),
                                "check": (
                                    "test -f proof.txt || "
                                    "{ echo FAIL: proof.txt was not created; exit 1; }"
                                ),
                            },
                            {
                                "key": "healthy-task",
                                "engine": "mock",
                                "spec": (
                                    "You are the deterministic mock worker. Write only the file "
                                    "described in this MOCK_FILE block so the executed check can "
                                    "verify the sibling lane survives.\n"
                                    "MOCK_FILE: hello.txt\n"
                                    "hello from mock\n"
                                    "MOCK_END"
                                ),
                                "check": (
                                    "grep -q hello hello.txt || "
                                    "{ echo FAIL: hello.txt missing hello; exit 1; }"
                                ),
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["RINGER_HOME"] = str(ringer_home)
            env["XDG_CONFIG_HOME"] = str(root / "xdg-config")
            env["RINGER_TEST_ROOT"] = str(root)

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
                    "vanished-test",
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
            # Exit 1 = "run finished, some task failed". Exit 2 was the crash.
            self.assertEqual(1, proc.returncode, combined_output)
            self.assertNotIn("ringer.py: error:", combined_output)
            self.assertRegex(
                combined_output,
                re.compile(r"^destroy-task\s+fail\s+FAIL\s+1\s+", re.MULTILINE),
                combined_output,
            )
            self.assertRegex(
                combined_output,
                re.compile(r"^healthy-task\s+pass\s+PASS\s+1\s+", re.MULTILINE),
                combined_output,
            )
            eval_rows = eval_log.read_text(encoding="utf-8")
            self.assertIn("task dir missing at verification", eval_rows)


if __name__ == "__main__":
    unittest.main()
