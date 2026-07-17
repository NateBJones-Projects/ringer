#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EvalConfig,
    aggregate_model_scoreboard_rows,
    build_models_api_payload,
    db_attempt_rows,
    invalidate_model_log_rows,
    read_model_log_rows,
    rebuild_read_model_db,
    run_models_command,
    sync_read_model_db,
    validate_since_date,
)


def attempt(
    run_id: str,
    task_key: str,
    *,
    model: str = "openrouter/acme/live",
    verdict: str = "PASS",
    task_type: str = "code-feature",
    logged_at: str = "2026-07-06T10:00:00+00:00",
    invalidated: bool = False,
) -> dict[str, object]:
    row: dict[str, object] = {
        "run_id": run_id,
        "task_key": task_key,
        "worker_engine": "opencode",
        "model": model,
        "task_type": task_type,
        "verdict": verdict,
        "retry": False,
        "duration_ms": 100,
        "worker_tokens": 200,
        "logged_at": logged_at,
        "orchestrator": "tester",
    }
    if invalidated:
        row.update(
            {
                "evidence_excluded": True,
                "invalidated_at": "2026-07-01T00:00:00+00:00",
                "invalidation_reason": "old reason",
            }
        )
    return row


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class ModelInvalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.log_path = self.root / "models.jsonl"
        self.db_path = self.root / "models.db"
        self.config = AppConfig(
            path=None,
            identity_default=None,
            state_dir=self.root / "state",
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=self.log_path),
            engines={},
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(self.root / "live.html"),
                report_template=str(self.root / "report.html"),
                index_out=self.root / "index.html",
            ),
        )

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def model_args(self, **overrides: object) -> argparse.Namespace:
        values: dict[str, object] = {
            "log": self.log_path,
            "db": None,
            "task_type": None,
            "model": None,
            "engine": None,
            "since": None,
            "explore": False,
            "catalog_file": self.root / "missing-catalog.json",
            "notes_file": self.root / "missing-notes.md",
            "registry": self.root / "missing-registry.toml",
            "html": None,
            "open": False,
            "json": False,
            "invalidate": False,
            "invalidate_run_id": None,
            "invalidate_task_key": None,
            "reason": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_run_only_invalidation_marks_all_matching_rows_and_preserves_mode(self) -> None:
        write_jsonl(
            self.log_path,
            [
                attempt("run-1", "a"),
                attempt("run-1", "b", verdict="FAIL"),
                attempt("run-2", "a"),
            ],
        )
        os.chmod(self.log_path, 0o640)

        result = invalidate_model_log_rows(
            self.log_path,
            run_id="run-1",
            reason="bad harness",
            now=datetime(2026, 7, 17, 1, 2, 3, tzinfo=timezone.utc),
        )

        self.assertEqual((2, 2, 0), (result.matched, result.newly_invalidated, result.already_invalidated))
        self.assertEqual(0o640, self.log_path.stat().st_mode & 0o777)
        rows = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(rows[0]["evidence_excluded"])
        self.assertEqual("2026-07-17T01:02:03+00:00", rows[0]["invalidated_at"])
        self.assertEqual("bad harness", rows[1]["invalidation_reason"])
        self.assertNotIn("evidence_excluded", rows[2])

    def test_run_and_task_selection_and_byte_preservation(self) -> None:
        malformed = b"{not json}\n"
        unrelated = json.dumps(attempt("other", "a")).encode("utf-8") + b"\n"
        target = json.dumps(attempt("run-1", "a")).encode("utf-8") + b"\n"
        other_task = json.dumps(attempt("run-1", "b")).encode("utf-8") + b"\n"
        self.log_path.write_bytes(malformed + target + unrelated + other_task)

        result = invalidate_model_log_rows(self.log_path, run_id="run-1", task_key="a", reason="wrong task")

        self.assertEqual((1, 1, 0), (result.matched, result.newly_invalidated, result.already_invalidated))
        lines = self.log_path.read_bytes().splitlines(keepends=True)
        self.assertEqual(malformed, lines[0])
        self.assertEqual(unrelated, lines[2])
        self.assertEqual(other_task, lines[3])
        self.assertEqual("wrong task", json.loads(lines[1])["invalidation_reason"])

    def test_mixed_and_repeat_preserve_existing_invalidation_metadata(self) -> None:
        already_line = (
            b'{"run_id":"run-1","task_key":"old","worker_engine":"opencode","model":"openrouter/acme/live",'
            b'"task_type":"code-feature","evidence_excluded":true,"invalidated_at":"old-ts",'
            b'"invalidation_reason":"old reason","verdict":"PASS","logged_at":"2026-07-06T10:00:00+00:00"}\n'
        )
        live_line = json.dumps(attempt("run-1", "new")).encode("utf-8") + b"\n"
        self.log_path.write_bytes(already_line + live_line)

        first = invalidate_model_log_rows(self.log_path, run_id="run-1", reason="new reason")
        after_first = self.log_path.read_bytes()
        second = invalidate_model_log_rows(self.log_path, run_id="run-1", reason="repeat reason")

        self.assertEqual((2, 1, 1), (first.matched, first.newly_invalidated, first.already_invalidated))
        self.assertEqual((2, 0, 2), (second.matched, second.newly_invalidated, second.already_invalidated))
        self.assertEqual(after_first, self.log_path.read_bytes())
        self.assertEqual(already_line, after_first.splitlines(keepends=True)[0])

    def test_cli_rejects_missing_inputs_and_reports_no_match(self) -> None:
        write_jsonl(self.log_path, [attempt("run-1", "a")])

        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(2, run_models_command(self.config, self.model_args(invalidate=True, reason="x")))
        self.assertIn("requires --run", err.getvalue())

        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(2, run_models_command(self.config, self.model_args(invalidate=True, invalidate_run_id="run-1")))
        self.assertIn("non-empty --reason", err.getvalue())

        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = run_models_command(
                self.config,
                self.model_args(invalidate=True, invalidate_run_id="missing", reason="x"),
            )
        self.assertEqual(1, rc)
        self.assertIn("No matching model log rows", err.getvalue())

    def test_aggregates_exclude_invalidated_rows_but_keep_audit_counts_visible(self) -> None:
        rows = [
            attempt("run-1", "a", model="openrouter/acme/live", verdict="PASS"),
            attempt("run-2", "a", model="openrouter/acme/live", verdict="FAIL", invalidated=True),
            attempt("run-3", "a", model="openrouter/acme/invalid-only", verdict="PASS", invalidated=True),
        ]
        write_jsonl(self.log_path, rows)

        rollup = aggregate_model_scoreboard_rows(rows)
        by_model = {row["model"]: row for row in rollup}
        self.assertEqual(1, by_model["openrouter/acme/live"]["tasks"])
        self.assertEqual(1, by_model["openrouter/acme/live"]["attempts"])
        self.assertEqual(1, by_model["openrouter/acme/live"]["invalidated_rows"])
        self.assertEqual(1.0, by_model["openrouter/acme/live"]["pass_rate"])
        self.assertEqual(0, by_model["openrouter/acme/invalid-only"]["tasks"])
        self.assertEqual(0, by_model["openrouter/acme/invalid-only"]["attempts"])
        self.assertEqual(1, by_model["openrouter/acme/invalid-only"]["invalidated_rows"])
        self.assertEqual("unranked", by_model["openrouter/acme/invalid-only"]["tier"])

        payload = build_models_api_payload(
            log_path=self.log_path,
            default_log_path=self.root / "other.jsonl",
            catalog_path=self.root / "missing-catalog.json",
            registry_path=self.root / "missing-registry.toml",
            notes_path=self.root / "missing-notes.md",
        )
        api_by_model = {row["model"]: row for row in payload["rollup"]}
        self.assertEqual(1, api_by_model["openrouter/acme/live"]["invalidated_rows"])
        self.assertEqual(0, api_by_model["openrouter/acme/invalid-only"]["attempts"])

        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, run_models_command(self.config, self.model_args(json=True)))
        cli_groups = json.loads(out.getvalue())
        self.assertTrue(any(group["invalidated_rows"] == 1 for group in cli_groups))

    def test_db_rebuild_and_sync_stop_crediting_newly_invalidated_rows(self) -> None:
        write_jsonl(self.log_path, [attempt("run-1", "a"), attempt("run-2", "a", verdict="FAIL")])
        rebuild_read_model_db(self.db_path, self.log_path)

        result = invalidate_model_log_rows(self.log_path, run_id="run-2", reason="bad run")
        self.assertEqual(1, result.newly_invalidated)
        sync_result = sync_read_model_db(self.db_path, self.log_path)

        self.assertTrue(sync_result.rebuilt)
        db_rows, _registry = db_attempt_rows(self.db_path)
        by_model = {row["model"]: row for row in aggregate_model_scoreboard_rows(db_rows)}
        self.assertEqual(1, by_model["openrouter/acme/live"]["tasks"])
        self.assertEqual(1, by_model["openrouter/acme/live"]["attempts"])
        self.assertEqual(1, by_model["openrouter/acme/live"]["invalidated_rows"])
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])

    def test_invalidate_command_rebuilds_selected_default_read_model(self) -> None:
        write_jsonl(self.log_path, [attempt("run-1", "a"), attempt("run-2", "a", verdict="FAIL")])
        rebuild_read_model_db(self.db_path, self.log_path)

        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = run_models_command(
                self.config,
                self.model_args(
                    db=self.db_path,
                    invalidate=True,
                    invalidate_run_id="run-2",
                    reason="operator correction",
                ),
            )

        self.assertEqual(0, rc)
        self.assertIn("newly_invalidated=1 already_invalidated=0", out.getvalue())
        db_rows, _registry = db_attempt_rows(self.db_path)
        row = aggregate_model_scoreboard_rows(db_rows)[0]
        self.assertEqual(1, row["tasks"])
        self.assertEqual(1, row["invalidated_rows"])
        self.assertEqual(1.0, row["pass_rate"])

    def test_unchanged_sync_skips_prefix_hash_and_metadata_change_validates(self) -> None:
        write_jsonl(self.log_path, [attempt("run-1", "a")])
        rebuild_read_model_db(self.db_path, self.log_path)

        with mock.patch("ringer.hash_file_prefix", side_effect=AssertionError("should not hash")):
            unchanged = sync_read_model_db(self.db_path, self.log_path)
        self.assertFalse(unchanged.rebuilt)
        self.assertEqual(0, unchanged.attempts_inserted)

        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(attempt("run-2", "a")) + "\n")
        with mock.patch("ringer.hash_file_prefix", wraps=ringer.hash_file_prefix) as hashed:
            changed = sync_read_model_db(self.db_path, self.log_path)
        self.assertFalse(changed.rebuilt)
        self.assertGreaterEqual(hashed.call_count, 1)

    def test_since_keeps_malformed_missing_invalidated_rows_in_raw_path(self) -> None:
        since = validate_since_date("2026-07-10")
        rows = [
            attempt("run-1", "a", logged_at="2026-07-05T00:00:00+00:00"),
            attempt(
                "run-2",
                "a",
                verdict="FAIL",
                invalidated=True,
                logged_at="2026-07-15T00:00:00+00:00",
            ),
            attempt(
                "run-3",
                "a",
                verdict="FAIL",
                invalidated=True,
                logged_at="not-a-date",
            ),
            attempt(
                "run-4",
                "a",
                verdict="FAIL",
                invalidated=True,
                logged_at="",
            ),
        ]
        write_jsonl(self.log_path, rows)

        selected, skipped = read_model_log_rows(self.log_path, since=since)
        by_run = {row["run_id"]: row for row in selected}

        # run-1 group ends before --since, so it is excluded entirely.
        self.assertNotIn("run-1", by_run)
        # Valid invalidated row at/after --since is retained.
        self.assertIn("run-2", by_run)
        # Malformed and missing logged_at invalidated rows must not raise and
        # must be treated as not matching the --since filter (safely excluded).
        self.assertNotIn("run-3", by_run)
        self.assertNotIn("run-4", by_run)

    def test_since_keeps_malformed_missing_invalidated_rows_in_db_path(self) -> None:
        since = validate_since_date("2026-07-10")
        rows = [
            attempt("run-1", "a", logged_at="2026-07-05T00:00:00+00:00"),
            attempt(
                "run-2",
                "a",
                verdict="FAIL",
                invalidated=True,
                logged_at="2026-07-15T00:00:00+00:00",
            ),
            attempt(
                "run-3",
                "a",
                verdict="FAIL",
                invalidated=True,
                logged_at="not-a-date",
            ),
            attempt(
                "run-4",
                "a",
                verdict="FAIL",
                invalidated=True,
                logged_at="",
            ),
        ]
        write_jsonl(self.log_path, rows)
        rebuild_read_model_db(self.db_path, self.log_path)

        selected, _registry = db_attempt_rows(self.db_path, since=since)
        by_run = {row["run_id"]: row for row in selected}

        self.assertNotIn("run-1", by_run)
        self.assertIn("run-2", by_run)
        self.assertNotIn("run-3", by_run)
        self.assertNotIn("run-4", by_run)

    def test_same_size_prefix_metadata_change_forces_rebuild(self) -> None:
        base = attempt("run-1", "a", model="openrouter/acme/live", verdict="PASS")
        write_jsonl(self.log_path, [base])
        rebuild_read_model_db(self.db_path, self.log_path)

        # Simulate a same-size committed-prefix mutation: identical byte length
        # but changed metadata (verdict PASS -> FAIL) within the synced prefix.
        mutated = dict(base)
        mutated["verdict"] = "FAIL"
        payload = json.dumps(mutated) + "\n"
        # Same byte length as the original committed prefix line.
        self.assertEqual(len(payload), len(json.dumps(base) + "\n"))
        self.log_path.write_bytes(payload.encode("utf-8"))

        with mock.patch("ringer.hash_file_prefix", wraps=ringer.hash_file_prefix) as hashed:
            result = sync_read_model_db(self.db_path, self.log_path)
        self.assertGreaterEqual(hashed.call_count, 1)
        self.assertTrue(result.rebuilt)
        db_rows, _registry = db_attempt_rows(self.db_path)
        by_model = {row["model"]: row for row in aggregate_model_scoreboard_rows(db_rows)}
        # The prefix byte content changed (verdict metadata), so the rebuild
        # must reflect the corrected FAIL verdict instead of the stale PASS.
        self.assertIn("openrouter/acme/live", by_model)
        self.assertEqual(0.0, by_model["openrouter/acme/live"]["pass_rate"])

    def test_truncation_forces_rebuild(self) -> None:
        write_jsonl(
            self.log_path,
            [
                attempt("run-1", "a"),
                attempt("run-2", "a", verdict="FAIL"),
            ],
        )
        rebuild_read_model_db(self.db_path, self.log_path)

        # Truncate the log below the stored offset to force a rebuild.
        self.log_path.write_bytes((json.dumps(attempt("run-1", "a")) + "\n").encode("utf-8"))

        result = sync_read_model_db(self.db_path, self.log_path)
        self.assertTrue(result.rebuilt)
        db_rows, _registry = db_attempt_rows(self.db_path)
        self.assertEqual(1, len(db_rows))
        self.assertEqual("run-1", db_rows[0]["run_id"])


if __name__ == "__main__":
    unittest.main()
