#!/usr/bin/env python3
"""Pre-spawn setup failures must say what happened and how to fix it.

Observed in the field (2026-07-14): failed tasks keep their worktrees (by
design, for post-mortems), and a follow-up run with the same run_name then
died at 0.0s with verdict ERROR, an empty log, empty check output, and no
message anywhere naming the collision. A full diagnosis cycle later:
`git worktree list` showed the stale taskdir.

These tests pin the diagnostics: the collision message names the exact
unblocking command, the reason reaches the worker log and the run-state
record, and the summary lists setup failures explicitly.
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


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    for args in (
        ["git", "-C", str(path), "init", "--quiet"],
        ["git", "-C", str(path), "commit", "--allow-empty", "--quiet", "-m", "init"],
    ):
        subprocess.run(args, check=True, env=env, capture_output=True)


class SetupErrorDiagnosticsTests(unittest.TestCase):
    def run_with_stale_taskdir(self, *, registered_worktree: bool) -> tuple[str, Path, Path, Path, Path]:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            ringer_home = root / "ringer-home"
            state_dir = root / "state"
            workdir = root / "work"
            repo = root / "repo"
            config_path = root / "config.toml"
            manifest_path = root / "manifest.json"

            home.mkdir()
            ringer_home.mkdir()
            init_git_repo(repo)

            # Simulate what a previous failed run leaves behind: either a real
            # registered worktree (the field case) or a plain directory.
            stale_taskdir = workdir / "stale-task"
            if registered_worktree:
                stale_taskdir.parent.mkdir(parents=True)
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "add", "--quiet", str(stale_taskdir)],
                    check=True,
                    capture_output=True,
                )
            else:
                stale_taskdir.mkdir(parents=True)

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
                        "run_name": "setup-error-test",
                        "workdir": str(workdir),
                        "max_parallel": 1,
                        "worktrees": True,
                        "repo": str(repo),
                        "tasks": [
                            {
                                "key": "stale-task",
                                "engine": "mock",
                                "spec": (
                                    "You are the deterministic mock worker. This spec "
                                    "would succeed if the worker ran — the point of the "
                                    "test is that setup fails first.\n"
                                    "MOCK_FILE: hello.txt\n"
                                    "hello\n"
                                    "MOCK_END"
                                ),
                                "check": (
                                    "grep -q hello hello.txt || "
                                    "{ echo FAIL: hello.txt missing; exit 1; }"
                                ),
                                "expect_files": ["hello.txt"],
                            }
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
            env["RINGER_NO_SELF_UPDATE"] = "1"

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
                    "setup-error-test",
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
            expected_reason = (
                "worktree taskdir already exists"
                if registered_worktree
                else "taskdir already exists but is not a registered git worktree"
            )

            # Verdict ERROR as before — but no longer naked.
            self.assertRegex(
                combined_output,
                re.compile(r"^stale-task\s+fail\s+ERROR\s+1\s+", re.MULTILINE),
                combined_output,
            )

            # The summary lists the setup failure explicitly.
            self.assertIn(
                "setup failures (no worker was spawned):", combined_output
            )
            self.assertIn(expected_reason, combined_output)

            # The reason reaches the worker log, where post-mortems look first.
            worker_log_candidates = list(workdir.rglob("*.log"))
            self.assertTrue(worker_log_candidates, "no worker log written")
            logged = "".join(
                p.read_text(encoding="utf-8") for p in worker_log_candidates
            )
            self.assertIn(
                "task setup failed before any worker could spawn", logged
            )

            # The run-state record carries setup_error for the HUD/post-mortem.
            state_files = [
                p
                for p in state_dir.rglob("*.json")
                if "stale-task" in p.read_text(encoding="utf-8", errors="replace")
            ]
            self.assertTrue(state_files, "no run state file mentions the task")
            found_setup_error = False
            for state_file in state_files:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                tasks = data.get("tasks") if isinstance(data, dict) else None
                for task in tasks or []:
                    if task.get("key") == "stale-task" and expected_reason in (
                        task.get("setup_error") or ""
                    ):
                        found_setup_error = True
            self.assertTrue(
                found_setup_error,
                f"setup_error missing from run state: {state_files}",
            )

            return (
                combined_output,
                stale_taskdir.resolve(),
                workdir.resolve(),
                state_dir.resolve(),
                repo.resolve(),
            )

    def test_stale_registered_worktree_names_the_exact_remove_command(self) -> None:
        combined_output, stale_taskdir, _, _, repo = self.run_with_stale_taskdir(
            registered_worktree=True
        )
        # The command must be paste-safe from anywhere: repo-qualified and
        # pointing at the resolved taskdir.
        self.assertIn(
            f"git -C {repo} worktree remove --force {stale_taskdir}",
            combined_output,
        )

    def test_plain_directory_collision_does_not_claim_a_worktree_command(self) -> None:
        combined_output, _, _, _, _ = self.run_with_stale_taskdir(
            registered_worktree=False
        )
        # `git worktree remove` would fail on a plain directory — never
        # print a recovery command that does not work.
        self.assertNotIn("git worktree remove", combined_output)
        self.assertIn("move or delete it, then re-run", combined_output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
