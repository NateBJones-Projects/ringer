#!/usr/bin/env python3
"""Oracle for per-task check timeouts (OA-140).

A fixed CHECK_TIMEOUT_S = 60 fails any check that boots real infrastructure
(a docker-backed test stack takes ~90s), marking good workers failed. These
tests pin the contract for the optional per-task `check_timeout_s` field:

- absent  -> behavior identical to today (module constant CHECK_TIMEOUT_S,
             consulted at call time so tests can shrink it)
- present -> positive int, threaded into the check's asyncio.wait_for
- the timeout message names the limit AND where it came from, so retry
  prompts and post-mortems show whether the cap was chosen or inherited
- lint warns when a check looks like it boots infrastructure (generic
  patterns only: docker / compose up) while check_timeout_s is unset

Set RINGER_SLOW_ORACLE=1 to also run the ticket-verbatim slow cases
(a real 90s check against the real 60s default).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer as ringer_module  # noqa: E402
from ringer import Manifest, TaskSpec, Verifier, lint_manifest  # noqa: E402

LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)

PLAIN_CHECK = (
    "test -s output.txt && grep -q 'ready' output.txt || "
    "{ echo 'FAIL: output.txt missing or does not contain ready'; exit 1; }"
)

STACK_BOOT_CHECK = (
    "docker compose up -d && ./scripts/wait-healthy.sh && pytest tests/ || "
    "{ echo 'FAIL: stack tests failed'; exit 1; }"
)


def task_obj(key: str = "one", **overrides: object) -> dict[str, object]:
    obj: dict[str, object] = {
        "key": key,
        "spec": LONG_SPEC,
        "check": PLAIN_CHECK,
        "expect_files": ["output.txt"],
        "verified": "output.txt exists and contains ready",
        "task_type": "probe",
    }
    obj.update(overrides)
    return obj


class TaskSpecCheckTimeoutTests(unittest.TestCase):
    def test_absent_field_parses_as_none(self) -> None:
        task = TaskSpec.from_obj(task_obj())
        self.assertIsNone(task.check_timeout_s)

    def test_present_field_parses_as_int(self) -> None:
        task = TaskSpec.from_obj(task_obj(check_timeout_s=120))
        self.assertEqual(task.check_timeout_s, 120)

    def test_nonpositive_field_rejected(self) -> None:
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                TaskSpec.from_obj(task_obj(check_timeout_s=bad))


class VerifierCheckTimeoutTests(unittest.TestCase):
    def verify(self, task: TaskSpec) -> "ringer_module.VerifyResult":
        with tempfile.TemporaryDirectory() as tmp:
            return asyncio.run(Verifier().verify(task, Path(tmp)))

    def test_task_check_timeout_is_honored(self) -> None:
        # sleep 3 passes under the 60s default; with a 1s per-task cap the
        # check must time out, and quickly (proof the cap was threaded, not
        # the constant).
        task = TaskSpec.from_obj(
            task_obj(check="sleep 3 && echo ok", check_timeout_s=1, expect_files=[])
        )
        start = time.monotonic()
        result = self.verify(task)
        elapsed = time.monotonic() - start
        self.assertTrue(result.check_timed_out)
        self.assertFalse(result.ok)
        self.assertLess(elapsed, 3.0)

    def test_timeout_message_names_task_source(self) -> None:
        task = TaskSpec.from_obj(
            task_obj(check="sleep 3 && echo ok", check_timeout_s=1, expect_files=[])
        )
        result = self.verify(task)
        self.assertIn("check timed out after 1s", result.raw_output_excerpt)
        self.assertIn("task check_timeout_s", result.raw_output_excerpt)

    def test_timeout_message_names_default_source(self) -> None:
        # The default path must consult the module constant at call time;
        # shrink it so the default-timeout branch is provable in seconds.
        original = ringer_module.CHECK_TIMEOUT_S
        ringer_module.CHECK_TIMEOUT_S = 1
        try:
            task = TaskSpec.from_obj(
                task_obj(check="sleep 3 && echo ok", expect_files=[])
            )
            result = self.verify(task)
        finally:
            ringer_module.CHECK_TIMEOUT_S = original
        self.assertTrue(result.check_timed_out)
        self.assertIn("check timed out after 1s", result.raw_output_excerpt)
        self.assertIn("default", result.raw_output_excerpt)
        self.assertNotIn("task check_timeout_s", result.raw_output_excerpt)

    def test_generous_task_timeout_lets_slow_check_pass(self) -> None:
        original = ringer_module.CHECK_TIMEOUT_S
        ringer_module.CHECK_TIMEOUT_S = 1
        try:
            task = TaskSpec.from_obj(
                task_obj(check="sleep 3 && echo ok", check_timeout_s=30, expect_files=[])
            )
            result = self.verify(task)
        finally:
            ringer_module.CHECK_TIMEOUT_S = original
        self.assertFalse(result.check_timed_out)
        self.assertEqual(result.check_returncode, 0)
        self.assertTrue(result.ok)


class LintStackBootTests(unittest.TestCase):
    def manifest(self, tasks: list[dict[str, object]]) -> Manifest:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return Manifest.from_obj(
            {
                "run_name": "check-timeout-lint-test",
                "workdir": str(Path(temp_dir.name) / "work"),
                "max_parallel": 1,
                "worktrees": False,
                "tasks": tasks,
            }
        )

    def findings_mentioning_check_timeout(self, tasks: list[dict[str, object]]) -> list[str]:
        findings = lint_manifest(self.manifest(tasks), allow_noncanonical_route=True)
        return [f for f in findings if "check_timeout_s" in f]

    def test_warns_on_stack_boot_check_without_field(self) -> None:
        findings = self.findings_mentioning_check_timeout(
            [task_obj(check=STACK_BOOT_CHECK)]
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("one:", findings[0])

    def test_no_warning_when_field_is_set(self) -> None:
        findings = self.findings_mentioning_check_timeout(
            [task_obj(check=STACK_BOOT_CHECK, check_timeout_s=300)]
        )
        self.assertEqual(findings, [])

    def test_no_warning_on_plain_check(self) -> None:
        findings = self.findings_mentioning_check_timeout([task_obj()])
        self.assertEqual(findings, [])


@unittest.skipUnless(
    os.environ.get("RINGER_SLOW_ORACLE") == "1",
    "ticket-verbatim slow oracle; set RINGER_SLOW_ORACLE=1 (runs ~2.5 min)",
)
class SlowTicketOracleTests(unittest.TestCase):
    """OA-140 oracle, verbatim: sleep 90 && echo ok against the REAL default."""

    def verify(self, task: TaskSpec) -> "ringer_module.VerifyResult":
        with tempfile.TemporaryDirectory() as tmp:
            return asyncio.run(Verifier().verify(task, Path(tmp)))

    def test_90s_check_fails_with_default(self) -> None:
        task = TaskSpec.from_obj(
            task_obj(check="sleep 90 && echo ok", expect_files=[])
        )
        result = self.verify(task)
        self.assertTrue(result.check_timed_out)
        self.assertFalse(result.ok)

    def test_90s_check_passes_with_check_timeout_120(self) -> None:
        task = TaskSpec.from_obj(
            task_obj(check="sleep 90 && echo ok", check_timeout_s=120, expect_files=[])
        )
        result = self.verify(task)
        self.assertFalse(result.check_timed_out)
        self.assertEqual(result.check_returncode, 0)
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
