from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "templates" / "fix-swarm" / "checks" / "fix-swarm.py"


class FixSwarmTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="fix-swarm-template-")
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.workdir = self.root / "work"
        self.workdir.mkdir()
        self.repo.mkdir()
        self.git("init")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )

    def commit_base(self, path: str, content: str | bytes) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-m", "base")

    def write_summary(self) -> None:
        (self.repo / "fix-summary.md").write_text(
            "\n".join(
                [
                    "# Fix Summary",
                    "",
                    "## Summary",
                    "Template validator test.",
                    "",
                    "## Files Changed",
                    "Changed the owned file.",
                    "",
                    "## Verification",
                    "Ran the validator command.",
                    "",
                    "## Assumptions",
                    "None.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def run_validator(
        self,
        *,
        key: str = "task",
        owned_files: str = "file.txt",
        verify_command: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.write_summary()
        command = verify_command or f"{shlex.quote(sys.executable)} -c {shlex.quote('raise SystemExit(0)')}"
        return subprocess.run(
            [
                sys.executable,
                str(CHECK_SCRIPT),
                "--verify-command",
                command,
                "--patch",
                str(self.workdir / f"{key}.patch"),
                "--summary",
                "fix-summary.md",
                "--exported-summary",
                str(self.workdir / f"{key}.summary.md"),
                "--owned-files",
                owned_files,
            ],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_first_run_writes_attempt1_and_canonical_with_identical_binary_patch(self) -> None:
        self.commit_base("payload.bin", b"base\x00old\n")
        (self.repo / "payload.bin").write_bytes(b"base\x00new\xff\n")

        result = self.run_validator(key="binary", owned_files="payload.bin")

        self.assertEqual(result.returncode, 0, result.stdout)
        canonical = self.workdir / "binary.patch"
        attempt1 = self.workdir / "binary.attempt1.patch"
        self.assertTrue(canonical.is_file())
        self.assertTrue(attempt1.is_file())
        self.assertEqual(canonical.read_bytes(), attempt1.read_bytes())
        self.assertIn(b"GIT binary patch", canonical.read_bytes())

    def test_second_changed_run_preserves_attempt1_and_updates_canonical_to_attempt2(self) -> None:
        self.commit_base("file.txt", "base\n")
        (self.repo / "file.txt").write_text("attempt one\n", encoding="utf-8")

        first = self.run_validator()
        self.assertEqual(first.returncode, 0, first.stdout)
        attempt1 = self.workdir / "task.attempt1.patch"
        attempt1_bytes = attempt1.read_bytes()

        (self.repo / "file.txt").write_text("attempt two\n", encoding="utf-8")
        second = self.run_validator()

        self.assertEqual(second.returncode, 0, second.stdout)
        canonical = self.workdir / "task.patch"
        attempt2 = self.workdir / "task.attempt2.patch"
        self.assertTrue(attempt2.is_file())
        self.assertEqual(attempt1.read_bytes(), attempt1_bytes)
        self.assertNotEqual(attempt1_bytes, attempt2.read_bytes())
        self.assertEqual(canonical.read_bytes(), attempt2.read_bytes())

    def test_attempt_numbering_uses_max_existing_attempt_and_ignores_lookalikes(self) -> None:
        self.commit_base("file.txt", "base\n")
        for name in (
            "gap.attempt1.patch",
            "gap.attempt3.patch",
            "gap.attempt4.patch.bak",
            "gap.attempt5.diff",
            "gap.attemptx.patch",
            "other.attempt99.patch",
        ):
            (self.workdir / name).write_text("existing\n", encoding="utf-8")
        (self.repo / "file.txt").write_text("changed\n", encoding="utf-8")

        result = self.run_validator(key="gap")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue((self.workdir / "gap.attempt4.patch").is_file())
        self.assertFalse((self.workdir / "gap.attempt2.patch").exists())
        self.assertEqual((self.workdir / "gap.patch").read_bytes(), (self.workdir / "gap.attempt4.patch").read_bytes())

    def test_verify_failure_does_not_report_success(self) -> None:
        self.commit_base("file.txt", "base\n")
        (self.repo / "file.txt").write_text("changed\n", encoding="utf-8")
        verify_command = f"{shlex.quote(sys.executable)} -c {shlex.quote('raise SystemExit(7)')}"

        result = self.run_validator(verify_command=verify_command)

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAIL [verify_command_failed]", result.stdout)
        self.assertNotIn("PASS [fix_contract]", result.stdout)


if __name__ == "__main__":
    unittest.main()
