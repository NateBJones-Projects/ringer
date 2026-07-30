#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform.startswith("linux"), "bubblewrap contract is Linux/WSL-only")
class LinuxSandboxWrapperTests(unittest.TestCase):
    def test_wrapper_allows_host_reads_and_workspace_writes_but_blocks_host_writes(self) -> None:
        if shutil.which("bwrap") is None:
            self.skipTest("bubblewrap is not installed")

        repo_root = Path(__file__).resolve().parents[1]
        wrapper = repo_root / "engines" / "opencode-sandboxed-linux.sh"
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            taskdir = root / "task"
            taskdir.mkdir()
            forbidden = root / "forbidden.txt"
            host_readable = root / "host-readable.txt"
            host_readable.write_text("host reads remain available\n", encoding="utf-8")
            fake_worker = root / "opencode"
            fake_worker.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "if [ -n \"${RINGER_TEST_SECRET:-}\" ]; then exit 43; fi\n"
                f"cat {host_readable} > {taskdir / 'read-copy.txt'}\n"
                f"printf 'allowed\\n' > {taskdir / 'allowed.txt'}\n"
                f"if printf 'blocked\\n' > {forbidden}; then exit 42; fi\n"
                f"printf '%s\\n' \"$HOME\" > {taskdir / 'sandbox-home.txt'}\n",
                encoding="utf-8",
            )
            fake_worker.chmod(0o755)

            env = os.environ.copy()
            env["OPENCODE_BIN"] = str(fake_worker)
            env["RINGER_TEST_SECRET"] = "must-not-cross-boundary"
            completed = subprocess.run(
                [str(wrapper), str(taskdir), "run", "ignored"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30,
                env=env,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stdout)
            self.assertEqual("allowed\n", (taskdir / "allowed.txt").read_text(encoding="utf-8"))
            self.assertEqual(
                "host reads remain available\n",
                (taskdir / "read-copy.txt").read_text(encoding="utf-8"),
            )
            self.assertFalse(forbidden.exists())
            sandbox_home = (taskdir / "sandbox-home.txt").read_text(encoding="utf-8").strip()
            self.assertIn("ringer-opencode-linux.", sandbox_home)
            self.assertFalse(Path(sandbox_home).exists(), "ephemeral home survived the run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
