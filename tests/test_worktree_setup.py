#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RINGER_PATH = ROOT / "ringer.py"
SPEC = importlib.util.spec_from_file_location("ringer_module_worktree_setup", RINGER_PATH)
assert SPEC is not None and SPEC.loader is not None
ringer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ringer
SPEC.loader.exec_module(ringer)


def toml_string(value: object) -> str:
    return json.dumps(str(value))


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Ringer Test",
            "GIT_AUTHOR_EMAIL": "ringer-test@example.invalid",
            "GIT_COMMITTER_NAME": "Ringer Test",
            "GIT_COMMITTER_EMAIL": "ringer-test@example.invalid",
        }
    )
    subprocess.run(["git", "-C", str(path), "init", "--quiet"], check=True, env=env)
    (path / ".gitignore").write_text("ignored-input.txt\n", encoding="utf-8")
    (path / "README.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", ".gitignore", "README.txt"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--quiet", "-m", "base"],
        check=True,
        env=env,
    )


class WorktreeSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="ringer-worktree-setup-")
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.ringer_home = self.root / "ringer-home"
        self.state_dir = self.root / "state"
        self.workdir = self.root / "work"
        self.repo = self.root / "repo"
        self.config_path = self.root / "config.toml"
        self.jsonl_path = self.root / "runs.jsonl"
        self.home.mkdir()
        self.ringer_home.mkdir()
        init_git_repo(self.repo)
        self.write_config()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_config(self) -> None:
        self.config_path.write_text(
            "\n".join(
                [
                    f"state_dir = {toml_string(self.state_dir)}",
                    "",
                    "[eval]",
                    'backend = "jsonl"',
                    f"jsonl_path = {toml_string(self.jsonl_path)}",
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

    def manifest_obj(self, *, tasks: list[dict[str, object]], **extra: object) -> dict[str, object]:
        data: dict[str, object] = {
            "run_name": "worktree-setup-test",
            "workdir": str(self.workdir),
            "max_parallel": 1,
            "worktrees": True,
            "repo": str(self.repo),
            "tasks": tasks,
        }
        data.update(extra)
        return data

    def write_manifest(self, manifest: dict[str, object]) -> Path:
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path

    def run_ringer(self, manifest: dict[str, object], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        path = self.write_manifest(manifest)
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["RINGER_HOME"] = str(self.ringer_home)
        env["XDG_CONFIG_HOME"] = str(self.root / "xdg-config")
        env["RINGER_NO_SELF_UPDATE"] = "1"
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(RINGER_PATH),
                "run",
                str(path),
                "--config",
                str(self.config_path),
                "--no-dashboard",
                "--identity",
                "worktree-setup-test",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )

    def read_rows(self) -> list[dict[str, object]]:
        if not self.jsonl_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.jsonl_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def read_final_state(self) -> dict[str, object]:
        state_files = sorted((self.state_dir / "runs").glob("*.json"))
        self.assertEqual(1, len(state_files), state_files)
        return json.loads(state_files[0].read_text(encoding="utf-8"))

    def task(self, key: str, *, check: str = 'test -s worker-output.txt') -> dict[str, object]:
        return {
            "key": key,
            "engine": "mock",
            "spec": "MOCK_FILE: worker-output.txt\nworker ran\nMOCK_END",
            "expect_files": ["worker-output.txt"],
            "check": check,
        }

    def test_hook_creates_ignored_input_before_check_and_logs_output(self) -> None:
        manifest = self.manifest_obj(
            worktree_setup=(
                "printf 'setup output marker\\n'; "
                "printf 'setup stderr marker\\n' >&2; "
                "printf 'generated input\\n' > ignored-input.txt"
            ),
            tasks=[
                self.task(
                    "input-task",
                    check=(
                        "git check-ignore -q ignored-input.txt && "
                        "grep -q 'generated input' ignored-input.txt && "
                        "grep -q 'worker ran' worker-output.txt"
                    ),
                )
            ],
        )

        result = self.run_ringer(manifest)

        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(["PASS"], [row["verdict"] for row in self.read_rows()])
        log = (self.workdir / "logs" / "input-task.worker.log").read_text(encoding="utf-8")
        self.assertIn("setup output marker", log)
        self.assertIn("setup stderr marker", log)
        self.assertIn("mock-worker: wrote 1 file(s)", log)

    def test_hook_runs_once_per_created_worktree(self) -> None:
        count_path = self.root / "setup-count.txt"
        manifest = self.manifest_obj(
            worktree_setup=f"pwd >> {shlex.quote(str(count_path))}",
            tasks=[self.task("one"), self.task("two")],
        )

        result = self.run_ringer(manifest)

        self.assertEqual(0, result.returncode, result.stdout)
        lines = count_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, len(lines), lines)
        self.assertEqual(2, len(set(lines)), lines)
        self.assertTrue(all(Path(line).name in {"one", "two"} for line in lines), lines)

    def test_failing_hook_uses_setup_error_and_does_not_spawn_worker(self) -> None:
        manifest = self.manifest_obj(
            worktree_setup="printf 'setup before fail\\n'; exit 7",
            tasks=[self.task("fail-setup")],
        )

        result = self.run_ringer(manifest)

        self.assertEqual(1, result.returncode, result.stdout)
        rows = self.read_rows()
        self.assertEqual(["ERROR"], [row["verdict"] for row in rows])
        self.assertIn("worktree_setup exited with code 7", rows[0]["notes"])
        self.assertIn("setup failures (no worker was spawned):", result.stdout)
        self.assertIn("worktree_setup exited with code 7", result.stdout)
        log = (self.workdir / "logs" / "fail-setup.worker.log").read_text(encoding="utf-8")
        self.assertIn("setup before fail", log)
        self.assertIn("task setup failed before any worker could spawn", log)
        self.assertNotIn("mock-worker: wrote", log)
        self.assertFalse((self.workdir / "fail-setup" / "worker-output.txt").exists())
        state = self.read_final_state()
        task_state = state["tasks"][0]
        self.assertEqual("ERROR", task_state["verdict"])
        self.assertIn("worktree_setup exited with code 7", task_state["setup_error"])

    def test_absent_hook_keeps_worktree_run_unchanged(self) -> None:
        manifest = self.manifest_obj(tasks=[self.task("no-hook")])

        result = self.run_ringer(manifest)

        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(["PASS"], [row["verdict"] for row in self.read_rows()])
        log = (self.workdir / "logs" / "no-hook.worker.log").read_text(encoding="utf-8")
        self.assertNotIn("worktree setup started", log)

    def test_hook_is_ignored_when_worktrees_are_disabled(self) -> None:
        marker = self.root / "setup-ran.txt"
        manifest = self.manifest_obj(
            worktrees=False,
            repo=None,
            worktree_setup=f"touch {shlex.quote(str(marker))}",
            tasks=[self.task("plain-task")],
        )

        result = self.run_ringer(manifest)

        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(["PASS"], [row["verdict"] for row in self.read_rows()])
        self.assertFalse(marker.exists())

    def test_manifest_validation_and_with_max_parallel_preserve_setup(self) -> None:
        task_obj = self.task("parse")
        with self.assertRaisesRegex(ValueError, "worktree_setup must be a string"):
            ringer.Manifest.from_obj(
                {
                    "run_name": "bad-setup",
                    "workdir": str(self.workdir),
                    "worktree_setup": ["not", "a", "string"],
                    "tasks": [task_obj],
                }
            )

        manifest = ringer.Manifest.from_obj(
            {
                "run_name": "parse-setup",
                "workdir": str(self.workdir),
                "max_parallel": 1,
                "worktree_setup": "  printf setup  ",
                "tasks": [task_obj],
            }
        )
        updated = manifest.with_max_parallel(3)
        self.assertEqual("  printf setup  ", manifest.worktree_setup)
        self.assertEqual("  printf setup  ", updated.worktree_setup)
        self.assertEqual(3, updated.max_parallel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
