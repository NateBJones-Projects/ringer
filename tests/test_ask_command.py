#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def toml_string(value: object) -> str:
    return json.dumps(str(value))


class AskCommandTests(unittest.TestCase):
    def write_config(
        self,
        root: Path,
        worker: Path,
        *,
        engine_name: str = "answer-mock",
    ) -> Path:
        config = root / "config.toml"
        config.write_text(
            "\n".join(
                [
                    f"state_dir = {toml_string(root / 'state')}",
                    "",
                    "[eval]",
                    'backend = "jsonl"',
                    f"jsonl_path = {toml_string(root / 'runs.jsonl')}",
                    "",
                    "[artifact]",
                    "enabled = false",
                    "",
                    f"[engines.{engine_name}]",
                    f"bin = {toml_string(sys.executable)}",
                    "args_template = [",
                    f"  {toml_string(worker)},",
                    '  "{spec}",',
                    "]",
                    "sandbox_args = []",
                    "full_access_args = []",
                ]
            ),
            encoding="utf-8",
        )
        return config

    def test_dry_run_selects_source_without_spawning_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            source = root / "long-notes.md"
            workdir = root / "request"
            source.write_text(
                ("Unrelated notes.\n" * 1_000)
                + "The launch decision is Wednesday with a smaller scope.\n"
                + ("More unrelated notes.\n" * 1_000),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "ask",
                    "What was the launch decision?",
                    "--source",
                    str(source),
                    "--max-packet-bytes",
                    "3000",
                    "--workdir",
                    str(workdir),
                    "--keep-packet",
                    "--dry-run",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )

            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn("No model call was made.", proc.stdout)
            self.assertIn("Wednesday with a smaller scope", (workdir / "packet.txt").read_text())
            report = json.loads((workdir / "packet-report.json").read_text())
            self.assertLessEqual(report["packet_bytes"], 3_000)
            self.assertFalse((workdir / "answer").exists())

    def test_ask_runs_one_clean_worker_and_does_not_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            state_dir = root / "state"
            workdir = root / "request"
            source = root / "notes.md"
            worker = root / "answer_worker.py"
            config = root / "config.toml"
            home.mkdir()
            source.write_text("The answer is: ship Wednesday.\n", encoding="utf-8")
            worker.write_text(
                "from pathlib import Path\n"
                "Path('answer.md').write_text('Ship Wednesday.\\n', encoding='utf-8')\n"
                "print('mock answer complete')\n",
                encoding="utf-8",
            )
            config.write_text(
                "\n".join(
                    [
                        f"state_dir = {toml_string(state_dir)}",
                        "",
                        "[eval]",
                        'backend = "jsonl"',
                        f"jsonl_path = {toml_string(root / 'runs.jsonl')}",
                        "",
                        "[artifact]",
                        "enabled = false",
                        "",
                        "[engines.answer-mock]",
                        f"bin = {toml_string(sys.executable)}",
                        "args_template = [",
                        f"  {toml_string(worker)},",
                        '  "{spec}",',
                        "]",
                        "sandbox_args = []",
                        "full_access_args = []",
                    ]
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["HOME"] = str(home)
            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "ask",
                    "What is the decision?",
                    "--source",
                    str(source),
                    "--engine",
                    "answer-mock",
                    "--config",
                    str(config),
                    "--workdir",
                    str(workdir),
                    "--identity",
                    "ask-test",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )

            combined = proc.stdout + proc.stderr
            self.assertEqual(0, proc.returncode, combined)
            self.assertEqual("Ship Wednesday.\n", (workdir / "answer" / "answer.md").read_text())
            self.assertIn("Ship Wednesday.", proc.stdout)
            worker_log = (workdir / "answer" / "worker.log").read_text()
            self.assertEqual(1, worker_log.count("[ringer.py] attempt 1 started"))
            self.assertNotIn("[ringer.py] attempt 2 started", worker_log)
            self.assertNotIn("What is the decision?", worker_log)
            state_files = list((state_dir / "runs").glob("*.json"))
            self.assertEqual(1, len(state_files))
            self.assertNotIn(
                "What is the decision?",
                state_files[0].read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                "What is the decision?",
                (root / "runs.jsonl").read_text(encoding="utf-8"),
            )
            self.assertFalse((workdir / "packet.txt").exists())

    def test_existing_answer_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            workdir = Path(temp_root) / "request"
            (workdir / "answer").mkdir(parents=True)
            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "ask",
                    "Answer this.",
                    "--workdir",
                    str(workdir),
                    "--dry-run",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
            self.assertEqual(2, proc.returncode)
            self.assertIn("refusing to reuse", proc.stderr)

    def test_missing_explicit_source_stops_before_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            worker = root / "worker.py"
            marker = root / "started.txt"
            worker.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('started')\n",
                encoding="utf-8",
            )
            config = self.write_config(root, worker)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            (root / "home").mkdir()
            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "ask",
                    "Answer from the source.",
                    "--source",
                    str(root / "missing.md"),
                    "--engine",
                    "answer-mock",
                    "--config",
                    str(config),
                    "--workdir",
                    str(root / "request"),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
            self.assertEqual(2, proc.returncode)
            self.assertIn("no model call was made", proc.stderr)
            self.assertFalse(marker.exists())

    def test_failed_worker_starts_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            worker = root / "worker.py"
            counter = root / "counter.txt"
            worker.write_text(
                "from pathlib import Path\n"
                f"p = Path({str(counter)!r})\n"
                "n = int(p.read_text()) if p.exists() else 0\n"
                "p.write_text(str(n + 1))\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            config = self.write_config(root, worker)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            (root / "home").mkdir()
            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "ask",
                    "Answer this.",
                    "--engine",
                    "answer-mock",
                    "--config",
                    str(config),
                    "--workdir",
                    str(root / "request"),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
            self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual("1", counter.read_text())

    def test_timed_out_worker_starts_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            worker = root / "worker.py"
            counter = root / "counter.txt"
            worker.write_text(
                "from pathlib import Path\n"
                "import time\n"
                f"p = Path({str(counter)!r})\n"
                "n = int(p.read_text()) if p.exists() else 0\n"
                "p.write_text(str(n + 1))\n"
                "time.sleep(10)\n",
                encoding="utf-8",
            )
            config = self.write_config(root, worker)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            (root / "home").mkdir()
            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "ask",
                    "Answer this.",
                    "--engine",
                    "answer-mock",
                    "--config",
                    str(config),
                    "--timeout-s",
                    "1",
                    "--workdir",
                    str(root / "request"),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=15,
            )
            self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual("1", counter.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
