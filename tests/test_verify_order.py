#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import shlex
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


class CheckLauncherTests(unittest.TestCase):
    def test_check_launcher_wraps_the_check_command(self) -> None:
        # `check` is an arbitrary shell command run over task-controlled output;
        # a task that plants a conftest.py/Makefile gets code-exec from an
        # auto-loading check (pytest/make). A configured launcher must receive
        # the check command and decide how (and whether) to run it, instead of
        # a bare `sh -c` in the taskdir — so the sandbox, not the task's planted
        # config, owns the checker context.
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            taskdir = root / "task"
            taskdir.mkdir()
            # Hostile auto-loaded config: `import conftest` in taskdir runs this.
            (taskdir / "conftest.py").write_text(
                f"open({str(root / 'PWNED')!r}, 'w').close()\n", encoding="utf-8"
            )
            check = f"{shlex.quote(sys.executable)} -c 'import conftest'"
            task = TaskSpec(key="t1", spec="noop", check=check, expect_files=())

            # Sanity: bare (no launcher) — the planted conftest.py DOES execute,
            # proving this is a real auto-load code-exec vector.
            asyncio.run(Verifier().verify(task, taskdir))
            self.assertTrue((root / "PWNED").exists())
            (root / "PWNED").unlink()

            invoked = root / "LAUNCHER_INVOKED"
            launcher = root / "launcher.sh"
            launcher.write_text(
                "#!/bin/sh\n"
                f'printf "%s\\n" "$@" > {shlex.quote(str(invoked))}\n'
                "exit 0\n",  # sandbox stand-in: does NOT run the check bare
                encoding="utf-8",
            )
            launcher.chmod(0o755)

            verifier = Verifier(check_launcher=(str(launcher),))
            asyncio.run(verifier.verify(task, taskdir))

            self.assertTrue(invoked.exists(), "check was not routed through the launcher")
            self.assertIn("import conftest", invoked.read_text(encoding="utf-8"))
            self.assertFalse(
                (root / "PWNED").exists(),
                "planted conftest.py executed — check ran bare, not via the launcher",
            )


if __name__ == "__main__":
    unittest.main()
