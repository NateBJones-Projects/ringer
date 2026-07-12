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
    def test_wrapper_allows_workspace_write_and_blocks_host_write(self) -> None:
        if shutil.which("bwrap") is None:
            self.skipTest("bubblewrap is not installed")

        repo_root = Path(__file__).resolve().parents[1]
        wrapper = repo_root / "engines" / "opencode-sandboxed-linux.sh"
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            taskdir = root / "task"
            taskdir.mkdir()
            forbidden = root / "forbidden.txt"
            host_secret = root / "host-secret.txt"
            host_secret.write_text("must stay outside sandbox\n", encoding="utf-8")
            fake_bin_dir = root / "fake-bin"
            fake_bin_dir.mkdir()
            fake_worker = fake_bin_dir / "opencode"
            fake_worker.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "if [ -n \"${RINGER_TEST_SECRET:-}\" ]; then exit 43; fi\n"
                f"if [ -e {host_secret} ]; then exit 44; fi\n"
                "printf 'allowed\\n' > /workspace/allowed.txt\n"
                f"if printf 'blocked\\n' > {forbidden}; then exit 42; fi\n",
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
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
