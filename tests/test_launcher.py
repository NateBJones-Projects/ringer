#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_direct_launcher_prefers_supported_named_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log = root / "launcher.log"
            launcher = bin_dir / "python3.14"
            launcher.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$RINGER_LAUNCHER_LOG\"\n"
                "if [ \"$1\" = -c ]; then exit 0; fi\n"
                "printf '%s\\n' 'usage: ringer.py'\n",
                encoding="utf-8",
            )
            launcher.chmod(0o755)
            env = os.environ | {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "RINGER_LAUNCHER_LOG": str(log),
            }

            result = subprocess.run(
                [str(ROOT / "ringer.py"), "--help"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("usage: ringer.py", result.stdout)
            self.assertIn("--help", log.read_text(encoding="utf-8"))

    def test_direct_launcher_falls_back_to_next_supported_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log = root / "launcher.log"
            (bin_dir / "python3.14").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            launcher = bin_dir / "python3.13"
            launcher.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = -c ]; then exit 0; fi\n"
                "printf '%s\\n' \"$*\" >> \"$RINGER_LAUNCHER_LOG\"\n"
                "printf '%s\\n' 'usage: ringer.py'\n",
                encoding="utf-8",
            )
            for interpreter in bin_dir.iterdir():
                interpreter.chmod(0o755)
            env = os.environ | {
                "PATH": str(bin_dir),
                "RINGER_LAUNCHER_LOG": str(log),
            }

            result = subprocess.run(
                [str(ROOT / "ringer.py"), "--help"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("usage: ringer.py", result.stdout)
            self.assertEqual(
                f"{ROOT / 'ringer.py'} --help\n", log.read_text(encoding="utf-8")
            )

    def test_direct_launcher_fails_cleanly_without_supported_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bin_dir = Path(temp) / "bin"
            bin_dir.mkdir()
            launcher = bin_dir / "python3"
            launcher.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            launcher.chmod(0o755)
            env = os.environ | {"PATH": str(bin_dir)}

            result = subprocess.run(
                [str(ROOT / "ringer.py"), "--help"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(1, result.returncode)
            self.assertEqual(
                "ringer requires Python 3.11+; tried python3.14, python3.13, python3.12, "
                "python3.11, and python3.\n",
                result.stderr,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
