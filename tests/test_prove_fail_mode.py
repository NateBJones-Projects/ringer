#!/usr/bin/env python3
"""`run --prove-fail` executes checks against declared known-bad states.

Pinned here:
  * a check that rejects known-bad work is proved and exits successfully
  * a check that accepts known-bad work is BROKEN and exits nonzero
  * missing coverage is skipped, while failed setup is an error
  * no workers spawn — a deliberately broken engine binary must not matter
  * baseline and prove-fail cannot run together
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
    (path / "README.md").write_text("hello prove-fail\n", encoding="utf-8")
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
        ["git", "-C", str(path), "add", "README.md"],
        [
            "git",
            "-C",
            str(path),
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "init",
        ],
    ):
        subprocess.run(args, check=True, env=env, capture_output=True)


class ProveFailModeTests(unittest.TestCase):
    def run_ringer(
        self,
        root: Path,
        tasks: list[dict[str, object]],
        *mode_args: str,
        worktrees: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path | None]:
        home = root / "home"
        ringer_home = root / "ringer-home"
        state_dir = root / "state"
        workdir = root / "work"
        config_path = root / "config.toml"
        manifest_path = root / "manifest.json"
        model_log = root / "runs.jsonl"
        repo = root / "repo" if worktrees else None

        home.mkdir()
        ringer_home.mkdir()
        if repo is not None:
            init_git_repo(repo)

        # The engine binary does not exist. Prove-fail must neither spawn it
        # nor be blocked by the startup engine preflight.
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
                    "[engines.missing]",
                    'bin = "/nonexistent/engine-binary"',
                    "args_template = [",
                    '  "{spec}",',
                    "]",
                    "sandbox_args = []",
                    "full_access_args = []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        manifest: dict[str, object] = {
            "run_name": "prove-fail-test",
            "workdir": str(workdir),
            "max_parallel": 2,
            "worktrees": worktrees,
            "tasks": tasks,
        }
        if repo is not None:
            manifest["repo"] = str(repo)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

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
                *mode_args,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        return proc, model_log, workdir, repo

    def test_proved_check_exits_zero_without_spawning_workers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            proc, model_log, workdir, repo = self.run_ringer(
                root,
                [
                    {
                        "key": "corrupt-file",
                        "engine": "missing",
                        "spec": "Replace the corrupt artifact with clean content.",
                        "known_bad": "printf 'corrupt\\n' > artifact.txt",
                        "check": (
                            "grep -q '^clean$' artifact.txt || "
                            "{ echo FAIL: expected clean content; exit 1; }"
                        ),
                    }
                ],
                "--prove-fail",
                worktrees=True,
            )
            output = proc.stdout + proc.stderr

            self.assertEqual(0, proc.returncode, output)
            self.assertRegex(
                output,
                re.compile(r"^corrupt-file\s+prove-fail: proved \(rc=1\)", re.MULTILINE),
                output,
            )
            self.assertIn("FAIL: expected clean content", output)
            self.assertIn(
                "prove-fail: 1 proved, 0 broken, 0 inconclusive, 0 error, "
                "0 skipped of 1 task(s).",
                output,
            )
            self.assertFalse(model_log.exists(), "prove-fail wrote model-log rows")
            self.assertFalse((root / "state").exists(), "prove-fail wrote run state")
            self.assertFalse(
                (workdir / "corrupt-file").exists(),
                "prove-fail leaked a taskdir into the manifest workdir",
            )
            assert repo is not None
            worktree_list = subprocess.run(
                ["git", "-C", str(repo), "worktree", "list"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(
                1,
                len(worktree_list.splitlines()),
                f"prove-fail leaked worktrees:\n{worktree_list}",
            )

    def test_check_that_cannot_fail_is_broken(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            proc, model_log, _, _ = self.run_ringer(
                Path(temp_root),
                [
                    {
                        "key": "false-pass",
                        "engine": "missing",
                        "spec": "A worker would repair the bad state.",
                        "known_bad": "printf 'bad\\n' > artifact.txt",
                        "check": "true",
                    }
                ],
                "--prove-fail",
            )
            output = proc.stdout + proc.stderr

            self.assertNotEqual(0, proc.returncode, output)
            self.assertRegex(
                output,
                re.compile(r"^false-pass\s+prove-fail: BROKEN", re.MULTILINE),
                output,
            )
            self.assertIn("check passed on a known-bad state", output)
            self.assertIn("0 proved, 1 broken", output)
            self.assertFalse(model_log.exists(), "prove-fail wrote model-log rows")

    def test_task_without_known_bad_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            proc, _, _, _ = self.run_ringer(
                Path(temp_root),
                [
                    {
                        "key": "uncovered",
                        "engine": "missing",
                        "spec": "This task has no prove-fail coverage yet.",
                        "check": "true",
                    }
                ],
                "--prove-fail",
            )
            output = proc.stdout + proc.stderr

            self.assertEqual(0, proc.returncode, output)
            self.assertRegex(
                output,
                re.compile(
                    r"^uncovered\s+prove-fail: skipped \(no known_bad\)",
                    re.MULTILINE,
                ),
                output,
            )
            self.assertIn("0 error, 1 skipped of 1 task(s).", output)

    def test_failing_known_bad_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            proc, _, _, _ = self.run_ringer(
                Path(temp_root),
                [
                    {
                        "key": "bad-setup",
                        "engine": "missing",
                        "spec": "A worker would repair the bad state.",
                        "known_bad": "echo could-not-create-bad-state; exit 7",
                        "check": "false",
                    }
                ],
                "--prove-fail",
            )
            output = proc.stdout + proc.stderr

            self.assertNotEqual(0, proc.returncode, output)
            self.assertRegex(
                output,
                re.compile(
                    r"^bad-setup\s+prove-fail: ERROR "
                    r"\(bad state could not be established; rc=7\)",
                    re.MULTILINE,
                ),
                output,
            )
            self.assertIn("could-not-create-bad-state", output)
            self.assertIn("0 inconclusive, 1 error, 0 skipped", output)

    def test_missing_deliverable_with_successful_check_is_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            proc, _, _, _ = self.run_ringer(
                Path(temp_root),
                [
                    {
                        "key": "missing-bad-deliverable",
                        "engine": "missing",
                        "spec": "A worker would create a valid deliverable.",
                        "known_bad": "true",
                        "check": "true",
                        "expect_files": ["artifact.txt"],
                    }
                ],
                "--prove-fail",
            )
            output = proc.stdout + proc.stderr

            self.assertNotEqual(0, proc.returncode, output)
            self.assertRegex(
                output,
                re.compile(
                    r"^missing-bad-deliverable\s+prove-fail: inconclusive",
                    re.MULTILINE,
                ),
                output,
            )
            self.assertIn("check never caught the bad state", output)
            self.assertIn(
                "known_bad must fabricate the deliverables in bad form", output
            )
            self.assertIn("0 broken, 1 inconclusive", output)

    def test_known_bad_must_be_a_string(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            proc, _, _, _ = self.run_ringer(
                Path(temp_root),
                [
                    {
                        "key": "invalid-known-bad",
                        "engine": "missing",
                        "spec": "This manifest field is malformed.",
                        "known_bad": ["false"],
                        "check": "false",
                    }
                ],
                "--prove-fail",
            )
            output = proc.stdout + proc.stderr

            self.assertNotEqual(0, proc.returncode, output)
            self.assertIn(
                "task invalid-known-bad: known_bad must be a string", output
            )

    def test_baseline_and_prove_fail_must_run_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            bad_marker = root / "known-bad-ran"
            check_marker = root / "check-ran"
            proc, _, _, _ = self.run_ringer(
                root,
                [
                    {
                        "key": "conflict",
                        "engine": "missing",
                        "spec": "Neither mode should execute this task.",
                        "known_bad": f"touch {bad_marker}",
                        "check": f"touch {check_marker}",
                    }
                ],
                "--baseline",
                "--prove-fail",
            )
            output = proc.stdout + proc.stderr

            self.assertNotEqual(0, proc.returncode, output)
            self.assertEqual(1, len(output.strip().splitlines()), output)
            self.assertIn(
                "--baseline and --prove-fail answer different questions; "
                "run them separately",
                output,
            )
            self.assertFalse(bad_marker.exists(), "prove-fail ran despite flag conflict")
            self.assertFalse(check_marker.exists(), "baseline ran despite flag conflict")


if __name__ == "__main__":
    unittest.main(verbosity=2)
