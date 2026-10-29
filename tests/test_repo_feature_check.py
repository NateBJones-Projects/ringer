"""check_repo_feature.py: staged-output harvest for write-confined engines.

Sandboxed workers (e.g. the opencode Seatbelt engine) cannot write to --repo.
The check installs files staged at <taskdir>/<stage-dir>/<repo-rel> into the
repo before validating, refusing anything outside --owned without copying a
single file. Born from the 2026-08-26 phone-cop-voicemail-fetch runs, where
two correct GLM deliverables burned both attempts against an untouched repo.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "templates" / "repo-feature" / "checks" / "check_repo_feature.py"
BUILD_OK = f'"{sys.executable}" -c "print(\'build-ok\')"'


def git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


class RepoFeatureCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = pathlib.Path(self._tmp.name)
        self.repo = root / "repo"
        self.taskdir = root / "task"
        (self.repo / "gate").mkdir(parents=True)
        self.taskdir.mkdir()
        (self.repo / "gate" / "vm_pull.py").write_text("BASE\n", encoding="utf-8")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        (self.taskdir / "notes.md").write_text("worker notes\n", encoding="utf-8")

    def stage(self, rel: str, content: str, stage_dir: str = "output/repo") -> pathlib.Path:
        path = self.taskdir / stage_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def run_check(self, owned: str = "gate/vm_pull.py,tests/test_vm_pull.py", timeout: float = 60, **kwargs: str) -> subprocess.CompletedProcess:
        args = {
            "--repo": str(self.repo),
            "--owned": owned,
            "--required-paths": kwargs.pop("required_paths", "gate/vm_pull.py"),
            "--required-text": kwargs.pop("required_text", ""),
            "--build-command": kwargs.pop("build_command", BUILD_OK),
            "--notes": "notes.md",
        }
        for key, value in kwargs.items():
            args["--" + key.replace("_", "-")] = value
        cmd = [sys.executable, str(SCRIPT)]
        for key, value in args.items():
            cmd.extend([key, value])
        return subprocess.run(cmd, cwd=self.taskdir, capture_output=True, text=True, timeout=timeout)

    def test_direct_repo_edit_still_passes_without_stage_dir(self) -> None:
        (self.repo / "gate" / "vm_pull.py").write_text("EDITED VMPULL-CASE-01\n", encoding="utf-8")
        proc = self.run_check(required_text="VMPULL-CASE-01")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PASS", proc.stdout)

    def test_staged_files_are_installed_into_repo_before_validation(self) -> None:
        self.stage("gate/vm_pull.py", "STAGED VMPULL-CASE-01\n")
        self.stage("tests/test_vm_pull.py", "STAGED TEST\n")
        proc = self.run_check(
            required_paths="gate/vm_pull.py,tests/test_vm_pull.py",
            required_text="VMPULL-CASE-01",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("harvest", proc.stdout)
        self.assertEqual((self.repo / "gate" / "vm_pull.py").read_text(encoding="utf-8"), "STAGED VMPULL-CASE-01\n")
        self.assertEqual((self.repo / "tests" / "test_vm_pull.py").read_text(encoding="utf-8"), "STAGED TEST\n")

    def test_staged_file_outside_owned_fails_and_installs_nothing(self) -> None:
        self.stage("gate/vm_pull.py", "STAGED\n")
        self.stage("gate/unowned.py", "SNEAKY\n")
        proc = self.run_check()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("gate/unowned.py", proc.stdout)
        self.assertEqual((self.repo / "gate" / "vm_pull.py").read_text(encoding="utf-8"), "BASE\n")
        self.assertFalse((self.repo / "gate" / "unowned.py").exists())

    def test_staged_git_path_is_refused(self) -> None:
        self.stage(".git/hooks/post-checkout", "#!/bin/sh\n")
        proc = self.run_check(owned=".git/hooks/post-checkout,gate/vm_pull.py")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(".git", proc.stdout)
        self.assertFalse((self.repo / ".git" / "hooks" / "post-checkout").exists())

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_staged_symlink_is_refused(self) -> None:
        target = self.taskdir / "secret.txt"
        target.write_text("secret\n", encoding="utf-8")
        link = self.taskdir / "output" / "repo" / "gate" / "vm_pull.py"
        link.parent.mkdir(parents=True)
        link.symlink_to(target)
        proc = self.run_check()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("symlink", proc.stdout)
        self.assertEqual((self.repo / "gate" / "vm_pull.py").read_text(encoding="utf-8"), "BASE\n")

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_staged_path_resolving_outside_repo_is_refused(self) -> None:
        outside = pathlib.Path(self._tmp.name) / "outside"
        outside.mkdir()
        (self.repo / "escape").symlink_to(outside)
        self.stage("escape/pwned.py", "PWNED\n")
        proc = self.run_check(owned="escape/pwned.py,gate/vm_pull.py")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("escape", proc.stdout)
        self.assertFalse((outside / "pwned.py").exists())

    def test_staged_file_colliding_with_repo_directory_is_refused(self) -> None:
        self.stage("gate", "I AM A FILE\n")
        proc = self.run_check(owned="gate,gate/vm_pull.py")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("directory", proc.stdout)
        self.assertTrue((self.repo / "gate").is_dir())

    def test_validation_failure_skips_build_and_prints_staging_hint(self) -> None:
        proc = self.run_check(required_paths="gate/vm_pull.py,tests/test_vm_pull.py")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("required repo path missing: tests/test_vm_pull.py", proc.stdout)
        self.assertNotIn("running build/test command", proc.stdout)
        self.assertIn("output/repo/<repo-relative-path>", proc.stdout)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_stage_root_is_refused(self) -> None:
        mirror = self.taskdir / "mirror" / "gate"
        mirror.mkdir(parents=True)
        (mirror / "vm_pull.py").write_text("MIRROR\n", encoding="utf-8")
        (self.taskdir / "output").mkdir()
        (self.taskdir / "output" / "repo").symlink_to(self.taskdir / "mirror")
        proc = self.run_check()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("symlink", proc.stdout)
        self.assertEqual((self.repo / "gate" / "vm_pull.py").read_text(encoding="utf-8"), "BASE\n")

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_destination_resolving_through_repo_symlink_is_refused(self) -> None:
        (self.repo / "gate" / "hidden.py").write_text("HIDDEN\n", encoding="utf-8")
        (self.repo / "gate" / "vm_pull.py").unlink()
        (self.repo / "gate" / "vm_pull.py").symlink_to(self.repo / "gate" / "hidden.py")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "symlinked owned path")
        self.stage("gate/vm_pull.py", "STAGED\n")
        proc = self.run_check()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("symlink", proc.stdout)
        self.assertEqual((self.repo / "gate" / "hidden.py").read_text(encoding="utf-8"), "HIDDEN\n")

    @unittest.skipIf(os.name == "nt", "mkfifo is POSIX-only")
    def test_staged_fifo_is_refused_not_hung(self) -> None:
        fifo_parent = self.taskdir / "output" / "repo" / "gate"
        fifo_parent.mkdir(parents=True)
        os.mkfifo(fifo_parent / "vm_pull.py")
        proc = self.run_check(timeout=20)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("regular file", proc.stdout)
        self.assertEqual((self.repo / "gate" / "vm_pull.py").read_text(encoding="utf-8"), "BASE\n")

    @unittest.skipIf(os.name == "nt", "directory write bits differ on Windows")
    def test_copy_failure_prints_clean_error_not_traceback(self) -> None:
        self.stage("tests/test_vm_pull.py", "STAGED TEST\n")
        blocked = self.repo / "tests"
        blocked.mkdir()
        blocked.chmod(0o555)
        self.addCleanup(blocked.chmod, 0o755)
        proc = self.run_check()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("harvest copy failed", proc.stdout)
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)

    def test_installed_files_get_fresh_mtimes_for_build_tools(self) -> None:
        staged = self.stage("gate/vm_pull.py", "STAGED VMPULL-CASE-01\n")
        os.utime(staged, (1000, 1000))
        proc = self.run_check(required_text="VMPULL-CASE-01")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertGreater((self.repo / "gate" / "vm_pull.py").stat().st_mtime, 1000)

    def test_stage_dir_empty_string_disables_harvest(self) -> None:
        self.stage("gate/vm_pull.py", "STAGED VMPULL-CASE-01\n")
        proc = self.run_check(stage_dir="", required_text="VMPULL-CASE-01")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual((self.repo / "gate" / "vm_pull.py").read_text(encoding="utf-8"), "BASE\n")


if __name__ == "__main__":
    unittest.main()
