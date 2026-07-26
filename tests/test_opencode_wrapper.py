#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "engines" / "opencode-sandboxed.sh"


class OpenCodeWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.taskdir = self.root / "task"
        self.bindir = self.root / "bin"
        self.taskdir.mkdir()
        self.bindir.mkdir()
        fake = self.bindir / "opencode"
        fake.write_text(
            "#!/bin/bash\n"
            "printf '%s\\n' \"${OPENCODE_DISABLE_CLAUDE_CODE:-unset}\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.bindir}:{self.env['PATH']}"
        self.env.pop("OPENCODE_DISABLE_CLAUDE_CODE", None)

    def run_wrapper(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(WRAPPER), str(self.taskdir), *args],
            cwd=self.taskdir,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def assert_compatibility_disabled(self, *args: str) -> None:
        result = self.run_wrapper(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1")

    def test_no_sandbox_path_disables_claude_compatibility(self) -> None:
        self.assert_compatibility_disabled("--no-sandbox")

    @unittest.skipUnless(Path("/usr/bin/sandbox-exec").is_file(), "macOS sandbox-exec required")
    def test_sandboxed_path_disables_claude_compatibility(self) -> None:
        self.assert_compatibility_disabled()


if __name__ == "__main__":
    unittest.main(verbosity=2)
