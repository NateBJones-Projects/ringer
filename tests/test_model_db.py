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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EvalConfig,
    connect_read_model_db,
    create_read_model_schema,
    humanized_log_date,
    load_model_identity_registry,
    rebuild_read_model_db,
    run_models_command,
    sync_read_model_db,
)


def attempt(
    *,
    run_id: str,
    task_key: str = "task",
    engine: str = "opencode",
    model: str = "openrouter/z-ai/glm-5.2",
    task_type: str = "code-feature",
    verdict: str = "PASS",
    retry: bool = False,
    logged_at: str = "2026-07-06T10:00:00+00:00",
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "task_key": task_key,
        "worker_engine": engine,
        "model": model,
        "task_type": task_type,
        "verdict": verdict,
        "retry": retry,
        "duration_ms": 100,
        "worker_tokens": 200,
        "logged_at": logged_at,
        "orchestrator": "tester",
    }


def write_jsonl(path: Path, rows: list[dict[str, object]], *, extra: str = "") -> None:
    body = "".join(json.dumps(row) + "\n" for row in rows)
    path.write_text(body + extra, encoding="utf-8")


def write_registry(path: Path) -> None:
    path.write_text(
        """
[engines.codex]
harness = "Codex CLI"
access = "OAuth plan"
default_model_key = "gpt-5.5"

[engines.codex.models."gpt-5.5"]
display = "GPT-5.5"
confidence = "unverified"
source = ""

[engines.opencode]
harness = "OpenCode"
access = "OpenRouter API"

[engines.opencode.models."openrouter/z-ai/glm-5.2"]
display = "GLM 5.2"
confidence = "verified"
source = "fixture"
""",
        encoding="utf-8",
    )


def write_catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "id": "openrouter/z-ai/glm-5.2",
                        "name": "GLM 5.2",
                        "context_length": 128000,
                        "prompt_per_m": 0.2,
                        "completion_per_m": 0.8,
                        "free": False,
                        "variable_pricing": False,
                        "pricing_unknown": False,
                        "fetched_at": "2026-07-06T00:00:00+00:00",
                        "modality": "text->text",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


class ModelDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.log_path = self.root / "runs.jsonl"
        self.db_path = self.root / "ringer.db"
        self.catalog_path = self.root / "catalog.json"
        self.registry_path = self.root / "model-identity.toml"
        write_catalog(self.catalog_path)
        write_registry(self.registry_path)

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def config(self) -> AppConfig:
        return AppConfig(
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

    def model_args(self, *, db_path: Path | None = None) -> argparse.Namespace:
        return argparse.Namespace(
            log=self.log_path,
            db=db_path or self.db_path,
            task_type=None,
            model=None,
            engine=None,
            since=None,
            explore=False,
            catalog_file=self.catalog_path,
            notes_file=self.root / "missing-notes.md",
            registry=self.registry_path,
            html=None,
            open=False,
            json=True,
        )

    def count_attempts(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])

    def test_schema_creation_uses_wal_mode(self) -> None:
        with contextlib.closing(connect_read_model_db(self.db_path)) as conn:
            create_read_model_schema(conn)
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
            conn.commit()

        self.assertEqual("wal", str(journal_mode).lower())
        self.assertEqual(3, user_version)
        self.assertEqual(3, version)

    def test_rebuild_ingests_rows_and_counts_skipped_lines(self) -> None:
        write_jsonl(
            self.log_path,
            [
                attempt(run_id="run-1"),
                attempt(run_id="run-2", verdict="FAIL", logged_at="2026-07-06T11:00:00+00:00"),
            ],
            extra="not-json\n",
        )

        result = rebuild_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )
        second = rebuild_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )

        self.assertEqual(2, result.attempts_inserted)
        self.assertEqual(1, result.skipped)
        self.assertEqual(2, second.attempts_inserted)
        self.assertEqual(2, self.count_attempts())
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM catalog_models").fetchone()[0])
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM identity").fetchone()[0])

    def test_sync_consumes_only_new_bytes_and_rebuilds_after_truncation(self) -> None:
        write_jsonl(self.log_path, [attempt(run_id="run-1")])
        rebuild_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(attempt(run_id="run-2")) + "\n")

        result = sync_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )
        again = sync_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )
        write_jsonl(self.log_path, [attempt(run_id="run-3")])
        truncated = sync_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )

        self.assertEqual(1, result.attempts_inserted)
        self.assertEqual(0, again.attempts_inserted)
        self.assertTrue(truncated.rebuilt)
        self.assertEqual(1, self.count_attempts())

    def test_sync_leaves_trailing_partial_line_for_next_pass(self) -> None:
        first_line = json.dumps(attempt(run_id="run-1")) + "\n"
        second_line = json.dumps(attempt(run_id="run-2"))
        cut_at = len(second_line) // 2
        partial_second = second_line[:cut_at]
        partial_start = len(first_line.encode("utf-8"))
        self.log_path.write_text(first_line + partial_second, encoding="utf-8")

        result = sync_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )

        self.assertEqual(1, result.attempts_inserted)
        self.assertEqual(0, result.skipped)
        self.assertEqual(partial_start, result.offset)
        self.assertEqual(1, self.count_attempts())

        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(second_line[cut_at:] + "\n")
        completed = sync_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )

        self.assertEqual(1, completed.attempts_inserted)
        self.assertEqual(2, self.count_attempts())
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            run_2_count = conn.execute(
                "SELECT COUNT(*) FROM attempts WHERE run_id = 'run-2'"
            ).fetchone()[0]
        self.assertEqual(1, run_2_count)

    def test_catalog_sync_skips_unchanged_files_and_appends_new_events_only(self) -> None:
        changes_path = ringer.catalog_changes_path(self.catalog_path)
        event_1 = {
            "ts": "2026-07-06T10:00:00+00:00",
            "kind": "price_change",
            "id": "openrouter/z-ai/glm-5.2",
            "old_prompt_per_m": 0.3,
            "new_prompt_per_m": 0.2,
        }
        event_2 = {
            "ts": "2026-07-06T11:00:00+00:00",
            "kind": "price_change",
            "id": "openrouter/z-ai/glm-5.2",
            "old_completion_per_m": 0.9,
            "new_completion_per_m": 0.8,
        }
        changes_path.write_text(json.dumps(event_1, sort_keys=True) + "\n", encoding="utf-8")
        write_jsonl(self.log_path, [attempt(run_id="run-1")])
        rebuild_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )

        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM catalog_events").fetchone()[0])
            before_state = dict(
                conn.execute(
                    "SELECT key, value FROM sync_state WHERE key LIKE 'catalog_%' ORDER BY key"
                ).fetchall()
            )
            conn.executescript(
                """
                CREATE TABLE catalog_write_audit(kind TEXT NOT NULL);
                CREATE TRIGGER audit_catalog_model_insert
                    AFTER INSERT ON catalog_models
                    BEGIN
                        INSERT INTO catalog_write_audit(kind) VALUES ('model_insert');
                    END;
                CREATE TRIGGER audit_catalog_model_delete
                    AFTER DELETE ON catalog_models
                    BEGIN
                        INSERT INTO catalog_write_audit(kind) VALUES ('model_delete');
                    END;
                CREATE TRIGGER audit_catalog_event_insert
                    AFTER INSERT ON catalog_events
                    BEGIN
                        INSERT INTO catalog_write_audit(kind) VALUES ('event_insert');
                    END;
                CREATE TRIGGER audit_catalog_event_delete
                    AFTER DELETE ON catalog_events
                    BEGIN
                        INSERT INTO catalog_write_audit(kind) VALUES ('event_delete');
                    END;
                """
            )

        sync_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM catalog_write_audit").fetchone()[0])
            after_state = dict(
                conn.execute(
                    "SELECT key, value FROM sync_state WHERE key LIKE 'catalog_%' ORDER BY key"
                ).fetchall()
            )
        self.assertEqual(before_state, after_state)

        with changes_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event_2, sort_keys=True) + "\n")
        sync_read_model_db(
            self.db_path,
            self.log_path,
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
        )
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM catalog_events").fetchone()[0])
            audit_counts = dict(
                conn.execute(
                    "SELECT kind, COUNT(*) FROM catalog_write_audit GROUP BY kind"
                ).fetchall()
            )
        self.assertEqual({"event_insert": 1}, audit_counts)

    def test_read_paths_do_not_create_or_stamp_schema_for_missing_tables(self) -> None:
        missing_db = self.root / "missing.db"

        with self.assertRaises(RuntimeError):
            ringer.db_catalog_models(missing_db)

        self.assertFalse(missing_db.exists())
        self.assertFalse(missing_db.with_name(missing_db.name + "-wal").exists())
        self.assertFalse(missing_db.with_name(missing_db.name + "-shm").exists())

        write_jsonl(self.log_path, [attempt(run_id="run-1")])
        with contextlib.closing(sqlite3.connect(self.db_path)):
            pass
        original_sync = ringer.sync_read_model_db

        def no_op_sync(*_args: object, **_kwargs: object) -> ringer.ReadModelSyncResult:
            return ringer.ReadModelSyncResult(
                self.db_path,
                self.log_path,
                attempts_inserted=0,
                skipped=0,
                offset=0,
                rebuilt=False,
            )

        ringer.sync_read_model_db = no_op_sync
        out = io.StringIO()
        err = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(0, run_models_command(self.config(), self.model_args()))
        finally:
            ringer.sync_read_model_db = original_sync

        payload = json.loads(out.getvalue())
        self.assertEqual(1, len(payload))
        self.assertIn("SQLite read model unavailable; using JSONL fallback", err.getvalue())
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            schema_row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
            ).fetchone()
        self.assertIsNone(schema_row)

    def test_registry_resolution_covers_defaults_openrouter_fallback_and_unknown_engine(self) -> None:
        registry = load_model_identity_registry(self.registry_path)

        codex = registry.resolve("codex", "")
        listed = registry.resolve("opencode", "openrouter/z-ai/glm-5.2")
        unlisted = registry.resolve("opencode", "openrouter/vendor/model")
        unknown = registry.resolve("custom-engine", "custom-model")

        self.assertEqual(("GPT-5.5", "Codex CLI", "OAuth plan"), (codex.model_display, codex.harness, codex.access))
        self.assertEqual(("GLM 5.2", "OpenCode", "OpenRouter API"), (listed.model_display, listed.harness, listed.access))
        self.assertEqual(("openrouter/vendor/model", "OpenCode", "OpenRouter API"), (unlisted.model_display, unlisted.harness, unlisted.access))
        # Unknown engine + model: display the MODEL slug, never the engine name.
        self.assertEqual(("custom-model", "custom-engine", "unknown"), (unknown.model_display, unknown.harness, unknown.access))

    def test_models_json_includes_identity_fields(self) -> None:
        write_jsonl(
            self.log_path,
            [
                attempt(run_id="run-1", engine="codex", model="", task_type="site-build"),
                attempt(run_id="run-2", engine="opencode", model="openrouter/vendor/model"),
            ],
        )
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            self.assertEqual(0, run_models_command(self.config(), self.model_args()))

        payload = json.loads(out.getvalue())
        by_model = {row["model"]: row for row in payload}
        # Blank-model log rows are quarantined, never credited to the engine's
        # default model (taxonomy contract, docs/TAXONOMY.md).
        legacy = by_model[""]
        self.assertEqual("(unattributed legacy rows)", legacy["model_display"])
        self.assertEqual("codex", legacy["engine"])
        self.assertTrue(legacy["unattributed"])
        self.assertNotIn("GPT-5.5", json.dumps(payload))
        self.assertEqual("Model", by_model["openrouter/vendor/model"]["model_display"])
        self.assertEqual("OpenCode", by_model["openrouter/vendor/model"]["harness"])
        self.assertEqual("OpenRouter API", by_model["openrouter/vendor/model"]["access"])
        self.assertEqual("vendor?", by_model["openrouter/vendor/model"]["lab"])

    def test_models_override_log_without_db_does_not_touch_default_db(self) -> None:
        fixture_log = self.root / "fixture-runs.jsonl"
        write_jsonl(fixture_log, [attempt(run_id="fixture-run")])
        default_db = Path(os.environ["RINGER_HOME"]) / "ringer.db"
        args = self.model_args()
        args.log = fixture_log
        args.db = None
        out = io.StringIO()
        err = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(0, run_models_command(self.config(), args))

        payload = json.loads(out.getvalue())
        self.assertEqual(1, len(payload))
        self.assertEqual("openrouter/z-ai/glm-5.2", payload[0]["model"])
        self.assertEqual("", err.getvalue())
        self.assertFalse(default_db.exists())
        self.assertFalse(default_db.with_name(default_db.name + "-wal").exists())
        self.assertFalse(default_db.with_name(default_db.name + "-shm").exists())

    def test_humanized_date_helper(self) -> None:
        self.assertEqual("last used: July 6, 2026", humanized_log_date("2026-07-06T10:00:00.123456+00:00", prefix="last used: "))
        self.assertEqual("July 6, 2026", humanized_log_date("2026-07-06"))

    def test_models_falls_back_when_db_path_is_unwritable(self) -> None:
        write_jsonl(self.log_path, [attempt(run_id="run-1")])
        not_a_directory = self.root / "not-a-directory"
        not_a_directory.write_text("file", encoding="utf-8")
        out = io.StringIO()
        err = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(0, run_models_command(self.config(), self.model_args(db_path=not_a_directory / "ringer.db")))

        payload = json.loads(out.getvalue())
        self.assertEqual(1, len(payload))
        self.assertIn("SQLite read model unavailable; using JSONL fallback", err.getvalue())
        self.assertEqual("GLM 5.2", payload[0]["model_display"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
