#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ringer import (
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    Manifest,
    RingerRunner,
    VerifyResult,
    WorkerResult,
    build_models_api_payload,
    create_read_model_schema,
    load_engines,
    parse_reported_model,
    print_model_log_table,
)


def attempt(model: str, *, engine: str = "codex") -> dict[str, object]:
    return {
        "run_id": f"run-{model}",
        "task_key": "task",
        "logged_at": "2026-07-10T12:00:00+00:00",
        "worker_engine": engine,
        "model": model,
        "task_type": "code-feature",
        "retry": False,
        "verdict": "PASS",
        "duration_ms": 10,
        "worker_tokens": 20,
        "orchestrator": "tester",
    }


class IdentityEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_environ = os.environ.copy()
        self.addCleanup(self.restore_environ)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.registry = self.root / "model-identity.toml"
        self.registry.write_text(
            """
[engines.codex]
harness = "Codex CLI"
access = "OAuth plan"
default_model_key = "old-default"

[engines.codex.models."old-default"]
display = "Old Registered Display"
lab = "OpenAI"
confidence = "verified"
source = "https://example.test/model"
last_verified = 2026-07-10

[engines.opencode]
harness = "OpenCode"
access = "OpenRouter API"
""",
            encoding="utf-8",
        )

    def restore_environ(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_environ)

    def config(self, log_path: Path, engine: EngineConfig) -> AppConfig:
        return AppConfig(
            path=None,
            identity_default=None,
            state_dir=self.root / "state",
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=log_path),
            engines={"codex": engine},
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(self.root / "live.html"),
                report_template=str(self.root / "report.html"),
                index_out=self.root / "index.html",
            ),
        )

    def write_log(self, path: Path, rows: list[dict[str, object]]) -> None:
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_codex_report_regex_captures_synthetic_header(self) -> None:
        engine = load_engines(None)["codex"]
        output = "OpenAI Codex v0.144.0\n--------\nmodel: gpt-5.6-sol\nprovider: openai\n"
        self.assertEqual("gpt-5.6-sol", parse_reported_model(output, engine.model_report_regex))

    def test_reported_model_wins_and_resolved_model_is_fallback(self) -> None:
        rows = self.log_attempts(
            WorkerResult(0, False, 12, reported_model="gpt-5.7"),
            WorkerResult(0, False, 12),
        )
        self.assertEqual("gpt-5.7", rows[0]["model"])
        self.assertEqual("gpt-5.7", rows[0]["reported_model"])
        self.assertEqual("gpt-5.6-sol", rows[0]["expected_model"])
        self.assertEqual("gpt-5.6-sol", rows[1]["model"])
        self.assertIsNone(rows[1]["reported_model"])
        self.assertIsNone(rows[1]["expected_model"])

    def test_mismatch_appends_identity_warning_to_worker_log(self) -> None:
        self.log_attempts(WorkerResult(0, False, 12, reported_model="gpt-5.7"))
        log = (self.root / "work" / "task" / "worker.log").read_text(encoding="utf-8")
        self.assertIn(
            "[ringer.py] identity: harness reported gpt-5.7 but manifest/config expected gpt-5.6-sol",
            log,
        )

    def log_attempts(self, *workers: WorkerResult) -> list[dict[str, object]]:
        log_path = self.root / "runs.jsonl"
        engine = EngineConfig(
            name="codex",
            bin="codex",
            args_template=("exec", "{model_args}", "{spec}"),
            full_access_args=(),
            sandbox_args=(),
            model_default="gpt-5.6-sol",
        )
        manifest = Manifest.from_obj(
            {
                "run_name": "identity-evidence",
                "workdir": str(self.root / "work"),
                "tasks": [
                    {
                        "key": "task",
                        "spec": "Create output.txt with one line and keep the change scoped.",
                        "check": "test -s output.txt || { echo 'output missing'; exit 1; }",
                    }
                ],
            }
        )
        runner = RingerRunner(
            manifest,
            config=self.config(log_path, engine),
            identity="tester",
            dashboard_enabled=False,
        )
        runtime = runner.runtimes[0]
        runtime.last_worker_command = ["codex", "exec", "-m", "gpt-5.6-sol"]
        verify = VerifyResult(True, 0, False, "ok")
        for worker in workers:
            runner._log_attempt(runtime, runtime.task.spec, False, worker, verify, "PASS", 10)
        return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    def test_schema_v3_migration_preserves_v2_attempt(self) -> None:
        db = self.root / "ringer.db"
        with sqlite3.connect(db) as conn:
            conn.executescript(
                """
                PRAGMA user_version = 2;
                CREATE TABLE schema_version(version INTEGER NOT NULL);
                INSERT INTO schema_version VALUES (2);
                CREATE TABLE attempts (
                    id INTEGER PRIMARY KEY, run_id TEXT, task_key TEXT, logged_at TEXT,
                    engine TEXT, model TEXT, reasoning_effort TEXT, task_type TEXT,
                    retry INTEGER, verdict TEXT, duration_ms INTEGER, worker_tokens INTEGER,
                    orchestrator TEXT
                );
                INSERT INTO attempts(model, verdict, reasoning_effort)
                VALUES ('gpt-5.6-sol', 'PASS', 'high');
                """
            )
            create_read_model_schema(conn)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
            self.assertTrue({"reported_model", "expected_model"}.issubset(columns))
            self.assertEqual(
                ("gpt-5.6-sol", "PASS", "high", None, None),
                conn.execute(
                    "SELECT model, verdict, reasoning_effort, reported_model, expected_model FROM attempts"
                ).fetchone(),
            )
            self.assertEqual(3, conn.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(3, conn.execute("SELECT version FROM schema_version").fetchone()[0])
            conn.execute(
                """
                INSERT INTO attempts(model, reported_model, expected_model, verdict)
                VALUES ('gpt-5.7', 'gpt-5.7', 'gpt-5.6-sol', 'PASS')
                """
            )
            self.assertEqual(
                ("gpt-5.7", "gpt-5.7", "gpt-5.6-sol"),
                conn.execute(
                    "SELECT model, reported_model, expected_model FROM attempts WHERE model = 'gpt-5.7'"
                ).fetchone(),
            )

    def test_unknown_slug_is_raw_unverified_unregistered_and_has_text_pointer(self) -> None:
        log_path = self.root / "unknown.jsonl"
        self.write_log(log_path, [attempt("new-model")])
        payload = build_models_api_payload(
            log_path=log_path,
            default_log_path=self.root / "other.jsonl",
            registry_path=self.registry,
            catalog_path=self.root / "missing.json",
        )
        row = payload["groups"][0]
        self.assertEqual(("new-model", "(unverified)", True), (
            row["model_display"], row["lab"], row["unregistered"]
        ))
        self.assertNotEqual("Old Registered Display", row["model_display"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_model_log_table(log_path, 1, 0, payload["groups"])
        self.assertTrue(out.getvalue().rstrip().endswith(
            "Unregistered model slug(s): new-model — run the identity procedure in docs/TAXONOMY.md."
        ))

    def test_openrouter_catalog_enriches_unregistered_identity_offline(self) -> None:
        log_path = self.root / "openrouter.jsonl"
        catalog_path = self.root / "catalog.json"
        self.write_log(log_path, [attempt("openrouter/z-ai/glm-5.2", engine="opencode")])
        catalog_path.write_text(json.dumps({"models": [{
            "id": "z-ai/glm-5.2", "name": "Z.AI: GLM 5.2"
        }]}), encoding="utf-8")
        payload = build_models_api_payload(
            log_path=log_path,
            default_log_path=self.root / "other.jsonl",
            registry_path=self.registry,
            catalog_path=catalog_path,
        )
        row = payload["groups"][0]
        self.assertEqual(("GLM 5.2", "Z.AI?", True), (
            row["model_display"], row["lab"], row["unregistered"]
        ))

    def test_last_verified_is_loaded_and_exposed_in_payload(self) -> None:
        log_path = self.root / "known.jsonl"
        self.write_log(log_path, [attempt("old-default")])
        payload = build_models_api_payload(
            log_path=log_path,
            default_log_path=self.root / "other.jsonl",
            registry_path=self.registry,
            catalog_path=self.root / "missing.json",
        )
        self.assertEqual("2026-07-10", payload["groups"][0]["last_verified"])
        self.assertFalse(payload["groups"][0]["unregistered"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
