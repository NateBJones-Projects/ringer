#!/usr/bin/env python3
"""Check-timeout budget: per-task override, config default, hard ceiling.

Regression cover for the defect where CHECK_TIMEOUT_S was a module constant with
no override path, so any check that legitimately ran longer than 60s (a real
`dotnet build && dotnet test`, a container start, a browser suite) was SIGTERM'd
and reported as TIMEOUT — a false negative on the gate that decides PASS.

The tests deliberately use short, scaled timings (fractions of a second) rather
than sleeping past a literal 60s: the property under test is "the budget that is
enforced is the *resolved* one", not the numeric value of the default.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    DEFAULT_CHECK_TIMEOUT_S,
    MAX_CHECK_TIMEOUT_S,
    AppConfig,
    TaskSpec,
    Verifier,
    resolve_check_timeout,
    verdict_for,
    WorkerResult,
)

LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)


def task(**overrides) -> TaskSpec:
    base = dict(key="t", spec=LONG_SPEC, check="true")
    base.update(overrides)
    return TaskSpec(**base)


class ResolveCheckTimeoutTests(unittest.TestCase):
    def test_default_is_unchanged_when_nothing_opts_in(self):
        """Backward compatibility: an untouched manifest behaves exactly as before."""
        self.assertEqual(60, DEFAULT_CHECK_TIMEOUT_S)
        self.assertEqual(DEFAULT_CHECK_TIMEOUT_S, resolve_check_timeout(task()))

    def test_task_field_wins_over_config_default(self):
        self.assertEqual(1800, resolve_check_timeout(task(check_timeout_s=1800), 300))

    def test_config_default_applies_when_task_is_silent(self):
        self.assertEqual(300, resolve_check_timeout(task(), 300))

    def test_resolution_is_clamped_to_the_hard_ceiling(self):
        """The gate must always terminate — no resolved value may exceed the ceiling."""
        self.assertEqual(MAX_CHECK_TIMEOUT_S, resolve_check_timeout(task(), MAX_CHECK_TIMEOUT_S * 10))

    def test_zero_or_negative_config_default_falls_back_rather_than_disabling_the_gate(self):
        self.assertEqual(DEFAULT_CHECK_TIMEOUT_S, resolve_check_timeout(task(), 0))


class TaskSpecParsingTests(unittest.TestCase):
    def test_check_timeout_is_absent_by_default(self):
        parsed = TaskSpec.from_obj({"key": "t", "spec": LONG_SPEC, "check": "true"})
        self.assertIsNone(parsed.check_timeout_s)

    def test_check_timeout_is_parsed(self):
        parsed = TaskSpec.from_obj(
            {"key": "t", "spec": LONG_SPEC, "check": "true", "check_timeout_s": 1800}
        )
        self.assertEqual(1800, parsed.check_timeout_s)

    def test_non_positive_check_timeout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "check_timeout_s must be positive"):
            TaskSpec.from_obj(
                {"key": "t", "spec": LONG_SPEC, "check": "true", "check_timeout_s": 0}
            )

    def test_check_timeout_above_ceiling_is_rejected_at_parse_time(self):
        with self.assertRaisesRegex(ValueError, "check_timeout_s must be <="):
            TaskSpec.from_obj(
                {
                    "key": "t",
                    "spec": LONG_SPEC,
                    "check": "true",
                    "check_timeout_s": MAX_CHECK_TIMEOUT_S + 1,
                }
            )


class ConfigTests(unittest.TestCase):
    def _config(self, body: str) -> AppConfig:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(body, encoding="utf-8")
            return AppConfig.load(path)

    def test_config_default_is_60_when_unset(self):
        self.assertEqual(DEFAULT_CHECK_TIMEOUT_S, self._config("").check_timeout_s)

    def test_config_can_raise_the_install_wide_default(self):
        self.assertEqual(1800, self._config("check_timeout_s = 1800\n").check_timeout_s)

    def test_config_rejects_a_value_above_the_ceiling(self):
        with self.assertRaisesRegex(ValueError, "check_timeout_s must be <="):
            self._config(f"check_timeout_s = {MAX_CHECK_TIMEOUT_S + 1}\n")


class VerifierEnforcementTests(unittest.TestCase):
    """The resolved budget is the one actually enforced against the subprocess."""

    def _verify(self, spec: TaskSpec, default: int = DEFAULT_CHECK_TIMEOUT_S):
        with tempfile.TemporaryDirectory() as tmp:
            verifier = Verifier(default_check_timeout_s=default)
            return asyncio.run(verifier.verify(spec, Path(tmp)))

    def test_check_outliving_the_old_hardcoded_default_is_permitted(self):
        """Requirement 1: a check may legitimately run past the default budget.

        Scaled: the check sleeps beyond a *small* default (0.2s) while its own
        task-level budget (10s) permits it. This is the exact shape of a .NET
        build outliving 60s under a 1800s check_timeout_s.
        """
        result = self._verify(
            task(check="sleep 1 && echo built && exit 0", check_timeout_s=10),
            default=1,
        )
        self.assertFalse(result.check_timed_out, "check was killed despite its own larger budget")
        self.assertTrue(result.ok)
        self.assertEqual(0, result.check_returncode)
        self.assertIn("built", result.raw_output_excerpt)

    def test_genuinely_hung_check_is_terminated(self):
        """Requirement 2: an unbounded check is still killed — the gate terminates."""
        result = self._verify(task(check="sleep 600", check_timeout_s=1))
        self.assertTrue(result.check_timed_out)
        self.assertFalse(result.ok)

    def test_timeout_is_reported_with_the_resolved_value_not_the_constant(self):
        """Requirement 3: output must name the budget that was actually enforced."""
        result = self._verify(task(check="sleep 600", check_timeout_s=1))
        self.assertIn("check timed out after 1s", result.raw_output_excerpt)
        self.assertNotIn(f"after {DEFAULT_CHECK_TIMEOUT_S}s", result.raw_output_excerpt)

    def test_config_default_is_enforced_when_task_is_silent(self):
        result = self._verify(task(check="sleep 600"), default=1)
        self.assertTrue(result.check_timed_out)
        self.assertIn("check timed out after 1s", result.raw_output_excerpt)

    def test_fast_check_still_fails_fast_and_is_not_blocked_by_a_large_budget(self):
        """Requirement 4 (guard): raising the ceiling must not slow a normal failure."""
        result = self._verify(task(check="echo nope; exit 3", check_timeout_s=1800))
        self.assertFalse(result.check_timed_out)
        self.assertFalse(result.ok)
        self.assertEqual(3, result.check_returncode)


@unittest.skipUnless(
    os.environ.get("RINGER_SLOW_TESTS") == "1",
    "slow: set RINGER_SLOW_TESTS=1 to run the literal >60s proof",
)
class SlowRealTimeoutTests(unittest.TestCase):
    """The literal requirement, unscaled: a check may exceed 60 real seconds.

    This is the shape of the original defect — before the fix, ANY check running
    past the hardcoded 60s was killed regardless of what the task declared.
    Opt-in because it costs ~65s of wall clock.
    """

    def test_check_running_past_sixty_real_seconds_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = task(check="sleep 65 && echo 'build+test done' && exit 0", check_timeout_s=1800)
            result = asyncio.run(Verifier().verify(spec, Path(tmp)))
        self.assertFalse(result.check_timed_out)
        self.assertTrue(result.ok)
        self.assertIn("build+test done", result.raw_output_excerpt)


class RetryContractTests(unittest.TestCase):
    """Retry behaviour must be untouched by this change."""

    def test_timeout_still_maps_to_the_timeout_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            verify = asyncio.run(
                Verifier(default_check_timeout_s=1).verify(task(check="sleep 600"), Path(tmp))
            )
        worker = WorkerResult(returncode=0, timed_out=False, tokens=None, error=None)
        # TIMEOUT is a retryable verdict in _run_task; the mapping must be stable.
        self.assertEqual("TIMEOUT", verdict_for(worker, verify))

    def test_check_failure_still_maps_to_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            verify = asyncio.run(
                Verifier().verify(task(check="echo bad; exit 1"), Path(tmp))
            )
        worker = WorkerResult(returncode=0, timed_out=False, tokens=None, error=None)
        self.assertEqual("FAIL", verdict_for(worker, verify))


if __name__ == "__main__":
    unittest.main()
