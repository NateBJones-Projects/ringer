from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SOURCE = ROOT / "ringer"


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="ringer-launcher-test-")
        self.root = Path(self.tmp.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.log_dir = self.root / "logs"
        self.log_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_repo(self, name: str = "repo") -> Path:
        repo = self.root / name
        repo.mkdir()
        shutil.copy2(LAUNCHER_SOURCE, repo / "ringer")
        (repo / "ringer").chmod((repo / "ringer").stat().st_mode | stat.S_IXUSR)
        (repo / "ringer.py").write_text("# launcher test stub\n", encoding="utf-8")
        return repo

    def write_fake_python(self, name: str, *, probe_exit: int = 0, run_exit: int = 0) -> None:
        path = self.bin_dir / name
        path.write_text(
            f"""#!/bin/sh
name=${{0##*/}}
if [ "$1" = "-c" ]; then
    printf '%s\\n' "$name" >> "$RINGER_FAKE_LOG/probes"
    exit {probe_exit}
fi
printf '%s\\n' "$name" > "$RINGER_FAKE_LOG/selected"
printf '%s\\0' "$@" > "$RINGER_FAKE_LOG/argv"
printf '%s\\n' "$PWD" > "$RINGER_FAKE_LOG/cwd"
exit {run_exit}
""",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_launcher(
        self, repo: Path, *args: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": str(self.bin_dir),
            "RINGER_FAKE_LOG": str(self.log_dir),
        }
        return subprocess.run(
            [str(repo / "ringer"), *args],
            cwd=str(cwd or self.root),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def probes(self) -> list[str]:
        path = self.log_dir / "probes"
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    def selected(self) -> str:
        return (self.log_dir / "selected").read_text(encoding="utf-8").strip()

    def forwarded_argv(self) -> list[str]:
        raw = (self.log_dir / "argv").read_bytes()
        return [part.decode("utf-8") for part in raw.split(b"\0")[:-1]]

    def test_prefers_highest_supported_python_command(self) -> None:
        repo = self.make_repo()
        self.write_fake_python("python3.13", run_exit=13)
        self.write_fake_python("python3.12", run_exit=12)
        self.write_fake_python("python3.11", run_exit=11)
        self.write_fake_python("python3", run_exit=3)

        result = self.run_launcher(repo, "demo")

        self.assertEqual(result.returncode, 13, result.stderr)
        self.assertEqual(self.probes(), ["python3.13"])
        self.assertEqual(self.selected(), "python3.13")

    def test_falls_back_from_broken_old_and_missing_candidates(self) -> None:
        repo = self.make_repo()
        self.write_fake_python("python3.13", probe_exit=2)
        self.write_fake_python("python3.12", probe_exit=1)
        self.write_fake_python("python3", run_exit=23)

        result = self.run_launcher(repo, "run", "swarm.json")

        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(self.probes(), ["python3.13", "python3.12", "python3"])
        self.assertEqual(self.selected(), "python3")

    def test_forwards_script_path_and_arguments_from_arbitrary_cwd(self) -> None:
        repo = self.make_repo("repo with spaces")
        caller_cwd = self.root / "caller cwd"
        caller_cwd.mkdir()
        self.write_fake_python("python3.11")

        result = self.run_launcher(
            repo,
            "run",
            "arg with spaces",
            "",
            "--flag=value with spaces",
            "semi;colon",
            cwd=caller_cwd,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.selected(), "python3.11")
        self.assertEqual(
            self.forwarded_argv(),
            [
                str((repo / "ringer.py").resolve()),
                "run",
                "arg with spaces",
                "",
                "--flag=value with spaces",
                "semi;colon",
            ],
        )
        self.assertEqual(
            (self.log_dir / "cwd").read_text(encoding="utf-8").strip(),
            str(caller_cwd.resolve()),
        )

    def test_errors_when_no_compatible_interpreter_exists(self) -> None:
        repo = self.make_repo()
        self.write_fake_python("python3.13", probe_exit=1)
        self.write_fake_python("python3.12", probe_exit=1)
        self.write_fake_python("python3.11", probe_exit=1)
        self.write_fake_python("python3", probe_exit=1)

        result = self.run_launcher(repo, "demo")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.probes(), ["python3.13", "python3.12", "python3.11", "python3"]
        )
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip(),
            "ringer: Python 3.11+ is required; install Python 3.11 or newer and ensure python3.11, python3.12, python3.13, or python3 is on PATH.",
        )
