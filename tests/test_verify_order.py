#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import TaskSpec, Verifier  # noqa: E402


LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)


class VerifyOrderTests(unittest.TestCase):
    def verify(self, task: TaskSpec, taskdir: Path):
        return asyncio.run(Verifier().verify(task, taskdir))

    def test_check_can_create_expected_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            taskdir = Path(root) / "task"
            taskdir.mkdir()
            task = TaskSpec(
                key="creates",
                spec=LONG_SPEC,
                check="echo made > out.txt && echo built",
                expect_files=("out.txt",),
            )
            result = self.verify(task, taskdir)

        self.assertTrue(result.ok, result.raw_output_excerpt)
        self.assertEqual((), result.missing_files)

    def test_missing_expected_file_after_successful_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            taskdir = Path(root) / "task"
            taskdir.mkdir()
            task = TaskSpec(
                key="missing",
                spec=LONG_SPEC,
                check="echo built",
                expect_files=("out.txt",),
            )
            result = self.verify(task, taskdir)

        self.assertFalse(result.ok)
        self.assertEqual(("out.txt",), result.missing_files)
        self.assertTrue(
            result.raw_output_excerpt.startswith("[ringer] missing expected files: out.txt"),
            result.raw_output_excerpt,
        )

    def test_vanished_taskdir_records_fail_instead_of_raising(self) -> None:
        # A worker can delete or replace its own taskdir (2026-07-09 run:
        # spawning the check there raised ENOENT and killed every lane).
        with tempfile.TemporaryDirectory() as root:
            taskdir = Path(root) / "task"  # intentionally never created
            task = TaskSpec(
                key="vanished",
                spec=LONG_SPEC,
                check="echo built",
                expect_files=("out.txt",),
            )
            result = self.verify(task, taskdir)

        self.assertFalse(result.ok)
        self.assertIsNone(result.check_returncode)
        self.assertEqual(("out.txt",), result.missing_files)
        self.assertIn("task dir missing at verification", result.raw_output_excerpt)
        self.assertIn(str(taskdir), result.raw_output_excerpt)

    def test_silent_failing_check_message_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            taskdir = Path(root) / "task"
            taskdir.mkdir()
            task = TaskSpec(
                key="silent",
                spec=LONG_SPEC,
                check="false",
            )
            result = self.verify(task, taskdir)

        self.assertFalse(result.ok)
        self.assertEqual((), result.missing_files)
        self.assertTrue(
            result.raw_output_excerpt.startswith("[ringer] check failed silently"),
            result.raw_output_excerpt,
        )


if __name__ == "__main__":
    unittest.main()
