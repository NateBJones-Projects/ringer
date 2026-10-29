#!/usr/bin/env python3
"""Terminal engine-level failures (billing exhausted, auth expired) must not
burn task attempts or count against a model's pass rate.

Regression for the 2026-07-12 incident: a Grok Build 402 "usage balance
exhausted" error caused every task on that engine to burn both attempts
(~5s each) before failing, and all of it landed in the scoreboard as
ordinary model failures.
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
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    aggregate_model_log_rows,
    aggregate_model_scoreboard_rows,
    detect_engine_down_reason,
)


def toml_string(value: object) -> str:
    return json.dumps(str(value))


class DetectEngineDownReasonTests(unittest.TestCase):
    def test_matches_the_incident_message(self) -> None:
        self.assertIsNotNone(
            detect_engine_down_reason("402 Payment Required: usage balance exhausted")
        )

    def test_matches_json_style_status_codes(self) -> None:
        self.assertIsNotNone(detect_engine_down_reason('{"error": {"status": 402}}'))
        self.assertIsNotNone(detect_engine_down_reason('{"error": {"code": 402}}'))

    def test_matches_auth_expired_variants(self) -> None:
        self.assertIsNotNone(detect_engine_down_reason("API key has expired"))
        self.assertIsNotNone(detect_engine_down_reason("your session expired, please log in"))
        self.assertIsNotNone(detect_engine_down_reason("token expired at 2026-07-12"))
        self.assertIsNotNone(detect_engine_down_reason("invalid_api_key: invalid API key"))

    def test_matches_insufficient_credit_phrasing(self) -> None:
        self.assertIsNotNone(detect_engine_down_reason("insufficient credits remaining"))
        self.assertIsNotNone(detect_engine_down_reason("Insufficient quota for this request"))

    def test_ignores_ordinary_worker_output(self) -> None:
        self.assertIsNone(detect_engine_down_reason("mock-worker: wrote 1 file(s): hello.txt"))
        self.assertIsNone(
            detect_engine_down_reason("diff shows 402 lines changed across the repo")
        )
        self.assertIsNone(detect_engine_down_reason(""))


class AggregationExcludesEngineDownTests(unittest.TestCase):
    """The scoreboard and model log must not treat ENGINE_DOWN like FAIL."""

    def rows(self) -> list[dict[str, object]]:
        return [
            {
                "run_id": "run1",
                "task_key": "a",
                "worker_engine": "grok-build",
                "model": "grok-build",
                "task_type": "code-feature",
                "verdict": "PASS",
                "duration_ms": 100,
                "worker_tokens": 10,
                "retry": False,
                "logged_at": "2026-07-12T10:00:00+00:00",
            },
            {
                "run_id": "run1",
                "task_key": "b",
                "worker_engine": "grok-build",
                "model": "grok-build",
                "task_type": "code-feature",
                "verdict": "ENGINE_DOWN",
                "duration_ms": 50,
                "worker_tokens": 0,
                "retry": False,
                "logged_at": "2026-07-12T10:00:05+00:00",
            },
            {
                "run_id": "run1",
                "task_key": "c",
                "worker_engine": "grok-build",
                "model": "grok-build",
                "task_type": "code-feature",
                "verdict": "ENGINE_DOWN",
                "duration_ms": 0,
                "worker_tokens": None,
                "retry": False,
                "logged_at": "2026-07-12T10:00:06+00:00",
            },
        ]

    def test_model_log_aggregation_excludes_engine_down_tasks(self) -> None:
        groups = aggregate_model_log_rows(self.rows())
        self.assertEqual(1, len(groups))
        group = groups[0]
        # Only the genuine PASS counts; the two ENGINE_DOWN tasks are excluded
        # entirely rather than counted as failures.
        self.assertEqual(1, group["tasks"])
        self.assertEqual(1, group["passed"])
        self.assertEqual(0, group["failed"])
        self.assertEqual(1.0, group["pass_rate"])
        self.assertEqual(1.0, group["first_try_pass_rate"])

    def test_model_scoreboard_aggregation_excludes_engine_down_tasks(self) -> None:
        entries = aggregate_model_scoreboard_rows(self.rows())
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual(1, entry["tasks"])
        self.assertEqual(1, entry["passed"])
        self.assertEqual(0, entry["failed"])
        self.assertEqual(1.0, entry["pass_rate"])


class EngineDownFastFailEndToEndTests(unittest.TestCase):
    def test_engine_down_short_circuits_remaining_tasks_without_burning_attempts(self) -> None:
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

            # max_parallel=1 makes the ordering deterministic: task-one burns
            # its one real (and only) attempt discovering the engine is down;
            # task-two and task-three must never even spawn a worker.
            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "engine-down-test",
                        "workdir": str(workdir),
                        "max_parallel": 1,
                        "worktrees": False,
                        "tasks": [
                            {
                                "key": "task-one",
                                "engine": "mock",
                                "spec": "You are the deterministic mock worker.\nMOCK_ENGINE_DOWN",
                                "check": "test -f impossible.txt || { echo FAIL: never runs; exit 1; }",
                            },
                            {
                                "key": "task-two",
                                "engine": "mock",
                                "spec": (
                                    "You are the deterministic mock worker. Write only the file "
                                    "described in this MOCK_FILE block.\n"
                                    "MOCK_FILE: hello.txt\n"
                                    "hello from mock\n"
                                    "MOCK_END"
                                ),
                                "check": "grep -q hello hello.txt || { echo FAIL: missing; exit 1; }",
                                "expect_files": ["hello.txt"],
                            },
                            {
                                "key": "task-three",
                                "engine": "mock",
                                "spec": (
                                    "You are the deterministic mock worker. Write only the file "
                                    "described in this MOCK_FILE block.\n"
                                    "MOCK_FILE: hello.txt\n"
                                    "hello from mock\n"
                                    "MOCK_END"
                                ),
                                "check": "grep -q hello hello.txt || { echo FAIL: missing; exit 1; }",
                                "expect_files": ["hello.txt"],
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
                    "engine-down-test",
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

            # All three tasks end in the distinct engine-down status, none of
            # them marked "fail" (which would count against the model).
            for key in ("task-one", "task-two", "task-three"):
                self.assertRegex(
                    combined_output,
                    re.compile(
                        rf"^{re.escape(key)}\s+engine-down\s+ENGINE_DOWN\s+1\s+",
                        re.MULTILINE,
                    ),
                    combined_output,
                )

            # task-one actually ran the mock worker and hit the 402.
            task_one_log = workdir / "task-one" / "worker.log"
            self.assertTrue(task_one_log.exists(), combined_output)
            self.assertIn("402 Payment Required", task_one_log.read_text(encoding="utf-8"))
            attempt_starts = re.findall(
                r"^\[ringer\.py\] attempt (\d) started \d{4}-",
                task_one_log.read_text(encoding="utf-8"),
                flags=re.MULTILINE,
            )
            self.assertEqual(["1"], attempt_starts, "task-one must not retry a dead engine")

            # task-two and task-three must never spawn a worker at all: no
            # taskdir, no log, no wasted attempt.
            self.assertFalse((workdir / "task-two").exists(), combined_output)
            self.assertFalse((workdir / "task-three").exists(), combined_output)

            # The model log must carry ENGINE_DOWN verdicts, not FAIL, so the
            # scoreboard doesn't count this as the model failing three tasks.
            log_rows = [
                json.loads(line)
                for line in (root / "runs.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(3, len(log_rows))
            for row in log_rows:
                self.assertEqual("ENGINE_DOWN", row["verdict"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
