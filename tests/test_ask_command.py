#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ringer
from ringer import TaskSpec


ROOT = Path(__file__).resolve().parents[1]


def toml_string(value: object) -> str:
    return json.dumps(str(value))


def cli_env(home: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["RINGER_NO_SELF_UPDATE"] = "1"
    env["RINGER_NO_CATALOG_REFRESH"] = "1"
    if home is not None:
        env["HOME"] = str(home)
    return env


class AskCommandTests(unittest.TestCase):
    def write_config(
        self,
        root: Path,
        worker: Path,
        *,
        engine_name: str = "answer-mock",
        artifact_enabled: bool = False,
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
                    f"enabled = {'true' if artifact_enabled else 'false'}",
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

    def run_cli(
        self,
        args: list[str],
        *,
        home: Path | None = None,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "ringer.py", *args],
            cwd=ROOT,
            env=cli_env(home),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )

    def run_in_process(
        self,
        args: list[str],
        *,
        home: Path,
    ) -> subprocess.CompletedProcess[str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, cli_env(home), clear=True),
            mock.patch.object(ringer.Dashboard, "start", return_value=8787),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            returncode = ringer.main(args)
        return subprocess.CompletedProcess(
            args,
            returncode,
            stdout.getvalue(),
            stderr.getvalue(),
        )

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
            proc = self.run_cli(
                [
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
                ]
            )

            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn("No model call was made.", proc.stdout)
            self.assertIn(str(source.resolve()), proc.stdout)
            self.assertIn(
                "Wednesday with a smaller scope",
                (workdir / "packet.txt").read_text(),
            )
            report = json.loads(
                (workdir / "packet-report.json").read_text(encoding="utf-8")
            )
            self.assertLessEqual(report["packet_bytes"], 3_000)
            self.assertFalse((workdir / "answer").exists())

    def test_default_keeps_request_visible_and_run_is_watched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            workdir = root / "request"
            source = root / "notes.md"
            worker = root / "answer_worker.py"
            home.mkdir()
            source.write_text(
                "The answer is: ship Wednesday.\n",
                encoding="utf-8",
            )
            worker.write_text(
                "from pathlib import Path\n"
                "Path('answer.md').write_text('Ship Wednesday.\\n', encoding='utf-8')\n"
                "print('RAW WORKER OUTPUT: mock answer complete')\n",
                encoding="utf-8",
            )
            config = self.write_config(
                root,
                worker,
                artifact_enabled=True,
            )
            request = "What is the visible decision?"
            proc = self.run_in_process(
                [
                    "ask",
                    request,
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
                home=home,
            )

            combined = proc.stdout + proc.stderr
            self.assertEqual(0, proc.returncode, combined)
            self.assertEqual(
                "Ship Wednesday.\n",
                (workdir / "answer" / "answer.md").read_text(),
            )
            self.assertIn("Ship Wednesday.", proc.stdout)
            worker_log = (workdir / "answer" / "worker.log").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                1,
                worker_log.count("[ringer.py] attempt 1 started"),
            )
            self.assertNotIn("[ringer.py] attempt 2 started", worker_log)
            self.assertIn(request, worker_log)
            self.assertIn("RAW WORKER OUTPUT: mock answer complete", worker_log)
            state_files = list((root / "state" / "runs").glob("*.json"))
            self.assertEqual(1, len(state_files))
            state = json.loads(state_files[0].read_text(encoding="utf-8"))
            self.assertIn(request, state["tasks"][0]["spec"])
            self.assertEqual(1, state["tasks"][0]["max_attempts"])
            self.assertIsInstance(state["dashboard_port"], int)
            self.assertIsNotNone(state["artifact_path"])
            self.assertIn(
                request,
                (root / "runs.jsonl").read_text(encoding="utf-8"),
            )
            library = json.loads(
                (root / "state" / "artifacts" / "library.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("one-request", library["artifacts"])
            self.assertFalse((workdir / "packet.txt").exists())

    def test_redact_hides_request_metadata_but_preserves_worker_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            workdir = root / "request"
            worker = root / "answer_worker.py"
            home.mkdir()
            worker.write_text(
                "from pathlib import Path\n"
                "Path('answer.md').write_text('Redacted answer.\\n', encoding='utf-8')\n"
                "print('RAW WORKER OUTPUT MUST REMAIN')\n",
                encoding="utf-8",
            )
            config = self.write_config(root, worker)
            request = "PRIVATE REQUEST PHRASE 82"
            proc = self.run_in_process(
                [
                    "ask",
                    request,
                    "--redact",
                    "--engine",
                    "answer-mock",
                    "--config",
                    str(config),
                    "--workdir",
                    str(workdir),
                    "--identity",
                    "ask-redaction-test",
                ],
                home=home,
            )

            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            worker_log = (workdir / "answer" / "worker.log").read_text(
                encoding="utf-8"
            )
            state_path = next((root / "state" / "runs").glob("*.json"))
            state_text = state_path.read_text(encoding="utf-8")
            eval_text = (root / "runs.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(request, worker_log)
            self.assertNotIn(request, state_text)
            self.assertNotIn(request, eval_text)
            self.assertIn("[request packet omitted]", worker_log)
            self.assertIn("[redacted request packet]", state_text)
            self.assertIn("[redacted request packet]", eval_text)
            self.assertIn("RAW WORKER OUTPUT MUST REMAIN", worker_log)

    def test_existing_answer_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            workdir = Path(temp_root) / "request"
            (workdir / "answer").mkdir(parents=True)
            proc = self.run_cli(
                [
                    "ask",
                    "Answer this.",
                    "--workdir",
                    str(workdir),
                    "--dry-run",
                ]
            )
            self.assertEqual(2, proc.returncode)
            self.assertIn("refusing to reuse", proc.stderr)

    def test_missing_explicit_source_stops_before_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            worker = root / "worker.py"
            marker = root / "started.txt"
            home.mkdir()
            worker.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('started')\n",
                encoding="utf-8",
            )
            config = self.write_config(root, worker)
            proc = self.run_cli(
                [
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
                home=home,
            )
            self.assertEqual(2, proc.returncode)
            self.assertIn("no model call was made", proc.stderr)
            self.assertFalse(marker.exists())

    def test_failed_worker_starts_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            worker = root / "worker.py"
            counter = root / "counter.txt"
            home.mkdir()
            worker.write_text(
                "from pathlib import Path\n"
                f"p = Path({str(counter)!r})\n"
                "n = int(p.read_text()) if p.exists() else 0\n"
                "p.write_text(str(n + 1))\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            config = self.write_config(root, worker)
            proc = self.run_in_process(
                [
                    "ask",
                    "Answer this.",
                    "--engine",
                    "answer-mock",
                    "--config",
                    str(config),
                    "--workdir",
                    str(root / "request"),
                    "--identity",
                    "ask-failure-test",
                ],
                home=home,
            )
            self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual("1", counter.read_text())

    def test_timed_out_worker_starts_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            worker = root / "worker.py"
            counter = root / "counter.txt"
            home.mkdir()
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
            proc = self.run_in_process(
                [
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
                    "--identity",
                    "ask-timeout-test",
                ],
                home=home,
            )
            self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual("1", counter.read_text())

    def test_max_attempts_parses_defaults_and_validates_positive(self) -> None:
        base = {
            "key": "one",
            "spec": "Do the work.",
            "check": "true",
        }
        self.assertEqual(2, TaskSpec.from_obj(base).max_attempts)
        self.assertEqual(
            3,
            TaskSpec.from_obj({**base, "max_attempts": 3}).max_attempts,
        )
        with self.assertRaisesRegex(ValueError, "max_attempts must be positive"):
            TaskSpec.from_obj({**base, "max_attempts": 0})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class NewFieldTypeStrictnessTests(unittest.TestCase):
    """`max_attempts` and `redact_spec` reject truthy stand-ins.

    `bool("false")` is True and `int(1.5)` is 1 — both would change what the
    manifest author asked for without saying anything.
    """

    def _task(self, **extra: object) -> dict[str, object]:
        base: dict[str, object] = {
            "key": "t",
            "spec": "a self-contained spec long enough to pass validation " * 2,
            "check": "true",
        }
        base.update(extra)
        return base

    def test_fractional_max_attempts_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as caught:
            TaskSpec.from_obj(self._task(max_attempts=1.5))
        self.assertIn("max_attempts must be an integer", str(caught.exception))

    def test_string_max_attempts_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TaskSpec.from_obj(self._task(max_attempts="2"))

    def test_string_redact_spec_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as caught:
            TaskSpec.from_obj(self._task(redact_spec="false"))
        self.assertIn("redact_spec must be true or false", str(caught.exception))

    def test_real_values_still_work(self) -> None:
        task = TaskSpec.from_obj(self._task(max_attempts=1, redact_spec=True))
        self.assertEqual(1, task.max_attempts)
        self.assertTrue(task.redact_spec)

    def test_defaults_are_unchanged(self) -> None:
        task = TaskSpec.from_obj(self._task())
        self.assertEqual(2, task.max_attempts)
        self.assertFalse(task.redact_spec)
