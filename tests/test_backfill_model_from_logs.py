#!/usr/bin/env python3
"""Command-log model backfill behavior."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backfill_model_from_logs.py"


class BackfillModelFromLogsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_dir = Path(self.temp.name) / "state"
        (self.state_dir / "runs").mkdir(parents=True)
        self.eval_log = self.state_dir / "runs.jsonl"

    def write_fixture(
        self,
        row: dict[str, object],
        *,
        command_lines: list[str] | None = None,
        run_id: str = "run-1",
        task_key: str = "task-a",
    ) -> Path:
        worker_log = Path(self.temp.name) / f"{run_id}-{task_key}.worker.log"
        lines = command_lines if command_lines is not None else []
        worker_log.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        state = {"tasks": [{"key": task_key, "log_path": str(worker_log)}]}
        (self.state_dir / "runs" / f"{run_id}.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        self.eval_log.write_text(json.dumps(row) + "\n", encoding="utf-8")
        return worker_log

    def run_script(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--state-dir", str(self.state_dir), *extra],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def read_rows(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.eval_log.read_text(encoding="utf-8").splitlines()]

    def test_stamps_from_last_command_log_evidence(self) -> None:
        self.write_fixture(
            {"run_id": "run-1", "task_key": "task-a", "model": "", "notes": "original"},
            command_lines=[
                "[ringer.py] command: codex exec -m old-model 'first spec'",
                "[ringer.py] command: codex exec --model=gpt-5.6-sol 'second spec' < /dev/null",
            ],
        )

        result = self.run_script()

        self.assertEqual(0, result.returncode, result.stderr)
        row = self.read_rows()[0]
        self.assertEqual("gpt-5.6-sol", row["model"])
        self.assertEqual("original\nmodel_backfill=command_log", row["notes"])
        self.assertIn("model '' -> 'gpt-5.6-sol'", result.stdout)
        self.assertIn("python3 ringer.py db rebuild --log", result.stdout)

    def test_does_not_guess_without_model_flag(self) -> None:
        original = {"run_id": "run-1", "task_key": "task-a", "notes": "keep"}
        self.write_fixture(
            original,
            command_lines=["[ringer.py] command: codex exec --sandbox workspace-write 'spec'"],
        )
        before = self.eval_log.read_bytes()

        result = self.run_script()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, self.eval_log.read_bytes())
        self.assertIn("no -m/--model flag", result.stdout)
        self.assertIn("stamped=0 no-evidence=1", result.stdout)
        self.assertEqual([], list(self.state_dir.glob("runs.jsonl.bak-*")))

    def test_latest_attempt_without_model_does_not_reuse_older_evidence(self) -> None:
        self.write_fixture(
            {"run_id": "run-1", "task_key": "task-a", "model": ""},
            command_lines=[
                "[ringer.py] command: codex exec -m old-model 'first spec'",
                "[ringer.py] command: codex exec --sandbox workspace-write 'latest spec'",
            ],
        )
        before = self.eval_log.read_bytes()

        result = self.run_script()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, self.eval_log.read_bytes())
        self.assertIn("no -m/--model flag", result.stdout)

    def test_dry_run_is_read_only_and_reports_change(self) -> None:
        worker_log = self.write_fixture(
            {"run_id": "run-1", "task_key": "task-a", "model": ""},
            command_lines=["[ringer.py] command: codex exec --model gpt-dry-run 'spec'"],
        )
        before_eval = self.eval_log.read_bytes()
        before_state = (self.state_dir / "runs" / "run-1.json").read_bytes()
        before_worker = worker_log.read_bytes()

        result = self.run_script("--dry-run")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("gpt-dry-run", result.stdout)
        self.assertIn("dry-run: no files written", result.stdout)
        self.assertEqual(before_eval, self.eval_log.read_bytes())
        self.assertEqual(before_state, (self.state_dir / "runs" / "run-1.json").read_bytes())
        self.assertEqual(before_worker, worker_log.read_bytes())
        self.assertEqual([], list(self.state_dir.glob("runs.jsonl.bak-*")))

    def test_real_run_creates_exact_backup(self) -> None:
        self.write_fixture(
            {"run_id": "run-1", "task_key": "task-a", "model": "", "notes": "n"},
            command_lines=["[ringer.py] command: codex exec -m backed-up 'spec'"],
        )
        before = self.eval_log.read_bytes()

        result = self.run_script()

        self.assertEqual(0, result.returncode, result.stderr)
        backups = list(self.state_dir.glob("runs.jsonl.bak-*"))
        self.assertEqual(1, len(backups))
        self.assertEqual(before, backups[0].read_bytes())
        self.assertNotEqual(before, self.eval_log.read_bytes())

    def test_second_run_is_idempotent(self) -> None:
        self.write_fixture(
            {"run_id": "run-1", "task_key": "task-a", "model": ""},
            command_lines=["[ringer.py] command: codex exec -m stable-model 'spec'"],
        )
        first = self.run_script()
        after_first = self.eval_log.read_bytes()
        backups_after_first = list(self.state_dir.glob("runs.jsonl.bak-*"))

        second = self.run_script()

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(after_first, self.eval_log.read_bytes())
        self.assertEqual(backups_after_first, list(self.state_dir.glob("runs.jsonl.bak-*")))
        self.assertIn("already-attributed=1", second.stdout)
        self.assertIn("no changes; runs.jsonl was not rewritten", second.stdout)

    def test_unparseable_line_passes_through_byte_for_byte(self) -> None:
        worker_log = Path(self.temp.name) / "worker.log"
        worker_log.write_text(
            "[ringer.py] command: codex exec -m recovered 'spec'\n", encoding="utf-8"
        )
        (self.state_dir / "runs" / "run-1.json").write_text(
            json.dumps({"tasks": [{"key": "task-a", "log_path": str(worker_log)}]}),
            encoding="utf-8",
        )
        malformed = b"not-json-\xff\r\n"
        valid = json.dumps(
            {"run_id": "run-1", "task_key": "task-a", "model": ""}
        ).encode("utf-8") + b"\n"
        self.eval_log.write_bytes(malformed + valid)

        result = self.run_script()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(self.eval_log.read_bytes().startswith(malformed))
        self.assertIn("unparseable line preserved", result.stdout)
        self.assertEqual("recovered", self.read_rows_from_bytes(self.eval_log.read_bytes()[len(malformed):])[0]["model"])

    @staticmethod
    def read_rows_from_bytes(raw: bytes) -> list[dict[str, object]]:
        return [json.loads(line) for line in raw.decode("utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
