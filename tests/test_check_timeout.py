#!/usr/bin/env python3
"""`check_timeout_s`: per-task budget for the check command.

Regression proof for making the check timeout configurable. The default was
a hardcoded 60s that silently killed any check running a real build/test
suite (a ~770-test pytest run measured at 140-240s reported as TIMEOUT even
though the underlying work was correct). The default is now 300s, overridable
per task; 0 is the sentinel meaning "use CHECK_TIMEOUT_S".
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import CHECK_TIMEOUT_S, TaskSpec  # noqa: E402


class CheckTimeoutTests(unittest.TestCase):
    def _task(self, **extra: object) -> dict[str, object]:
        base: dict[str, object] = {
            "key": "t",
            "spec": "a self-contained spec long enough to pass validation " * 2,
            "check": "true",
        }
        base.update(extra)
        return base

    def test_default_is_zero_meaning_use_global(self) -> None:
        # 0 is the sentinel; the check path resolves it to CHECK_TIMEOUT_S.
        task = TaskSpec.from_obj(self._task())
        self.assertEqual(0, task.check_timeout_s)
        self.assertEqual(CHECK_TIMEOUT_S, task.check_timeout_s or CHECK_TIMEOUT_S)

    def test_global_default_is_300(self) -> None:
        # The whole point of the fix: 60s silently killed real build/test
        # suites, so the floor is now 300s.
        self.assertEqual(300, CHECK_TIMEOUT_S)

    def test_explicit_value_parses_and_wins(self) -> None:
        task = TaskSpec.from_obj(self._task(check_timeout_s=900))
        self.assertEqual(900, task.check_timeout_s)
        self.assertEqual(900, task.check_timeout_s or CHECK_TIMEOUT_S)

    def test_negative_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "check_timeout_s must not be negative"):
            TaskSpec.from_obj(self._task(check_timeout_s=-1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
