#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RINGER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RINGER_DIR))

import ringer  # noqa: E402
from ringer import Manifest, TaskSpec, Verifier  # noqa: E402


LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)

GOOD_CHECK = (
    "test -s output.txt && grep -q 'ready' output.txt || "
    "{ echo 'FAIL: output.txt missing or does not contain ready'; exit 1; }"
)


def toml_string(value: object) -> str:
    return json.dumps(str(value))


def task_obj(key: str = "one", **overrides: object) -> dict[str, object]:
    obj: dict[str, object] = {
        "key": key,
        "spec": LONG_SPEC,
        "check": "echo ok",
    }
    obj.update(overrides)
    return obj


class CheckTimeoutTests(unittest.TestCase):
    def verify(self, task: TaskSpec, taskdir: Path):
        return asyncio.run(Verifier().verify(task, taskdir))

    def write_config(self, root: Path) -> Path:
        config_path = root / "config.toml"
        config_path.write_text(
            "\n".join(
                [
                    f"state_dir = {toml_string(root / 'state')}",
                    "",
                    "[eval]",
                    'backend = "jsonl"',
                    f"jsonl_path = {toml_string(root / 'runs.jsonl')}",
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
        return config_path

    def ringer_env(self, root: Path) -> dict[str, str]:
        home = root / "home"
        ringer_home = root / "ringer-home"
        home.mkdir(exist_ok=True)
        ringer_home.mkdir(exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["RINGER_HOME"] = str(ringer_home)
        env["XDG_CONFIG_HOME"] = str(root / "xdg-config")
        env["RINGER_NO_SELF_UPDATE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def run_ringer(self, args: list[str], *, root: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RINGER_DIR / "ringer.py"), *args],
            cwd=str(RINGER_DIR),
            env=self.ringer_env(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )

    def test_absent_field_stays_none_and_effective_is_default(self) -> None:
        task = TaskSpec.from_obj(task_obj("plain"))
        self.assertIsNone(task.check_timeout_s)
        self.assertEqual(60, task.effective_check_timeout_s)

    def test_from_obj_accepts_bounds(self) -> None:
        for value in (1, 3600):
            with self.subTest(value=value):
                task = TaskSpec.from_obj(task_obj("ok", check_timeout_s=value))
                self.assertEqual(value, task.check_timeout_s)
                self.assertEqual(value, task.effective_check_timeout_s)

    def test_from_obj_rejects_invalid_check_timeout_s(self) -> None:
        for value in (0, -5, 3601, 90.0, 2.5, "90", True, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    TaskSpec.from_obj(task_obj("bad-timeout", check_timeout_s=value))
                message = str(ctx.exception)
                self.assertIn("check_timeout_s", message)
                self.assertIn("bad-timeout", message)

    def test_per_task_timeout_overrides_patched_default(self) -> None:
        with mock.patch.object(ringer, "CHECK_TIMEOUT_S", 1):
            with tempfile.TemporaryDirectory() as root:
                taskdir = Path(root) / "task"
                taskdir.mkdir()
                task = TaskSpec.from_obj(
                    task_obj("override", check="sleep 2 && echo done", check_timeout_s=5)
                )
                result = self.verify(task, taskdir)
        self.assertTrue(result.ok, result.raw_output_excerpt)

    def test_default_timeout_is_read_at_runtime(self) -> None:
        with mock.patch.object(ringer, "CHECK_TIMEOUT_S", 1):
            with tempfile.TemporaryDirectory() as root:
                taskdir = Path(root) / "task"
                taskdir.mkdir()
                task = TaskSpec.from_obj(task_obj("defaulted", check="sleep 3"))
                result = self.verify(task, taskdir)
        self.assertFalse(result.ok)
        self.assertTrue(result.check_timed_out, result.raw_output_excerpt)

    def test_timeout_message_uses_task_limit(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            taskdir = Path(root) / "task"
            taskdir.mkdir()
            task = TaskSpec.from_obj(task_obj("short", check="sleep 5", check_timeout_s=1))
            result = self.verify(task, taskdir)
        self.assertFalse(result.ok)
        self.assertIn("check timed out after 1s", result.raw_output_excerpt)

    def test_manifest_from_obj_rejects_bad_task_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                Manifest.from_obj(
                    {
                        "run_name": "bad-check-timeout",
                        "workdir": tmp,
                        "tasks": [task_obj("one", check_timeout_s=0)],
                    }
                )
        self.assertIn("check_timeout_s", str(ctx.exception))

    def test_dry_run_listing_shows_effective_check_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            workdir = root / "work"
            config_path = self.write_config(root)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "check-timeout-dry-run",
                        "workdir": str(workdir),
                        "max_parallel": 1,
                        "worktrees": False,
                        "tasks": [
                            {
                                "key": "custom",
                                "engine": "missing",
                                "spec": LONG_SPEC,
                                "check": GOOD_CHECK,
                                "expect_files": ["output.txt"],
                                "verified": "the output file exists and contains the expected content",
                                "check_timeout_s": 45,
                            },
                            {
                                "key": "defaulted",
                                "engine": "missing",
                                "spec": LONG_SPEC,
                                "check": GOOD_CHECK,
                                "expect_files": ["output.txt"],
                                "verified": "the output file exists and contains the expected content",
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            proc = self.run_ringer(
                [
                    "run",
                    str(manifest_path),
                    "--dry-run",
                    "--config",
                    str(config_path),
                    "--no-dashboard",
                ],
                root=root,
            )
            combined = proc.stdout + proc.stderr
            self.assertEqual(0, proc.returncode, combined)
            self.assertIn("check_timeout_s: 45", combined)
            self.assertIn("check_timeout_s: 60", combined)

    def test_lint_rejects_string_check_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            workdir = root / "work"
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "check-timeout-lint",
                        "workdir": str(workdir),
                        "max_parallel": 1,
                        "worktrees": False,
                        "tasks": [
                            {
                                "key": "string-timeout",
                                "engine": "missing",
                                "spec": LONG_SPEC,
                                "check": GOOD_CHECK,
                                "expect_files": ["output.txt"],
                                "verified": "the output file exists and contains the expected content",
                                "check_timeout_s": "90",
                            }
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            proc = self.run_ringer(["lint", str(manifest_path)], root=root)
            combined = proc.stdout + proc.stderr
            self.assertNotEqual(0, proc.returncode, combined)
            self.assertIn("check_timeout_s", combined)


if __name__ == "__main__":
    unittest.main()
