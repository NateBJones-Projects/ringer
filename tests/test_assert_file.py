#!/usr/bin/env python3
"""scripts/assert-file — the behaviours a Ringer check depends on.

Each test runs the real script as a subprocess, because the thing under test IS
its command-line contract: a check invokes it from a shell and reads its exit
code. Asserting on an imported function would prove something a check never
exercises.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assert-file"


class AssertFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def write(self, name: str, body: str) -> Path:
        path = self.dir / name
        path.write_text(body, encoding="utf-8")
        return path

    def run_it(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True, capture_output=True, check=False,
        )

    def test_contains_passes_and_absent_fails_on_the_same_file(self) -> None:
        path = self.write("notes.md", "the report is complete\n")
        self.assertEqual(0, self.run_it(str(path), "--contains", "report").returncode)
        self.assertNotEqual(0, self.run_it(str(path), "--absent", "report").returncode)

    def test_comments_are_stripped_before_matching(self) -> None:
        # The originating bug: a brief asks the worker to explain itself in a
        # comment, the check greps for a banned word, and the worker's own
        # explanation trips it. Correct work, failed check.
        path = self.write("thing.py", "# we deliberately avoid sleep() here\nvalue = 1\n")
        self.assertEqual(0, self.run_it(str(path), "--absent", "sleep(").returncode)
        # --raw opts back in to matching the comment text.
        self.assertNotEqual(0, self.run_it(str(path), "--raw", "--absent", "sleep(").returncode)

    def test_failure_output_names_the_pattern_it_could_not_find(self) -> None:
        path = self.write("notes.md", "nothing useful here\n")
        result = self.run_it(str(path), "--contains", "conclusion")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("conclusion", result.stdout + result.stderr)

    def test_count_is_exact(self) -> None:
        path = self.write("log.txt", "hit\nhit\n")
        self.assertEqual(0, self.run_it(str(path), "--count", "hit=2").returncode)
        self.assertNotEqual(0, self.run_it(str(path), "--count", "hit=3").returncode)

    def test_before_checks_order_not_mere_presence(self) -> None:
        path = self.write("report.md", "## Findings\n## Conclusion\n")
        self.assertEqual(0, self.run_it(str(path), "--before", "Findings", "Conclusion").returncode)
        self.assertNotEqual(0, self.run_it(str(path), "--before", "Conclusion", "Findings").returncode)

    def test_a_missing_file_fails_rather_than_passing_vacuously(self) -> None:
        # A check that greps a file which was never written must not go green.
        missing = self.dir / "never-written.md"
        self.assertNotEqual(0, self.run_it(str(missing), "--contains", "anything").returncode)


if __name__ == "__main__":
    unittest.main()
