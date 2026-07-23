#!/usr/bin/env python3
"""Local per-model performance log behavior."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    EvalLogger,
    Manifest,
    RingerRunner,
    TaskSpec,
    VerifyResult,
    WorkerResult,
    aggregate_model_log_rows,
    model_log_row_is_retry,
    read_model_log_rows,
)

LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)
GOOD_CHECK = (
    "test -s output.txt && grep -q 'ready' output.txt || "
    "{ echo 'FAIL: output.txt missing or does not contain ready'; exit 1; }"
)


def harness_engine(model_default: str = "openrouter/z-ai/glm-5.2") -> EngineConfig:
    return EngineConfig(
        name="opencode",
        bin="/usr/local/bin/opencode",
        args_template=("run", "-m", "{model}", "--dir", "{taskdir}", "{spec}"),
        full_access_args=(),
        sandbox_args=(),
        token_regex=None,
        model_default=model_default,
    )


class ModelLogTests(unittest.TestCase):
    def config(self, root: Path) -> AppConfig:
        return AppConfig(
            path=None,
            identity_default=None,
            state_dir=root / "state",
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=root / "eval.jsonl"),
            engines={"opencode": harness_engine()},
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(root / "live.html"),
                report_template=str(root / "report.html"),
                index_out=root / "index.html",
            ),
        )

    def task_obj(self, **extra: object) -> dict[str, object]:
        task: dict[str, object] = {
            "key": "a",
            "spec": LONG_SPEC,
            "check": GOOD_CHECK,
            "engine": "opencode",
            "expect_files": ["output.txt"],
            "verified": "output exists with expected content",
        }
        task.update(extra)
        return task

    def test_task_spec_parses_and_validates_task_type(self) -> None:
        task = TaskSpec.from_obj(self.task_obj(task_type="  code-feature  "))
        self.assertEqual("code-feature", task.task_type)
        self.assertEqual("", TaskSpec.from_obj(self.task_obj()).task_type)
        with self.assertRaisesRegex(ValueError, "task_type must be a string"):
            TaskSpec.from_obj(self.task_obj(task_type=5))

    def test_eval_row_carries_model_task_type_and_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = Manifest.from_obj(
                {
                    "orch_task_id": "8e748b50-f5dc-48e8-bcb5-c193d4c95664",
                    "run_name": "model-log-test",
                    "workdir": str(root / "work"),
                    "tasks": [self.task_obj(task_type="code-feature")],
                }
            )
            runner = RingerRunner(
                manifest,
                config=self.config(root),
                identity="tester",
                dashboard_enabled=False,
            )
            runtime = runner.runtimes[0]
            runner._log_attempt(
                runtime,
                runtime.task.spec,
                True,
                WorkerResult(returncode=0, timed_out=False, tokens=123),
                VerifyResult(ok=True, check_returncode=0, check_timed_out=False, raw_output_excerpt="ok"),
                "PASS",
                456,
            )
            payload = json.loads((root / "eval.jsonl").read_text(encoding="utf-8"))
            self.assertEqual("openrouter/z-ai/glm-5.2", payload["model"])
            self.assertEqual("8e748b50-f5dc-48e8-bcb5-c193d4c95664", payload["orch_task_id"])
            self.assertEqual("code-feature", payload["task_type"])
            self.assertIs(payload["retry"], True)
            self.assertIn("model=openrouter/z-ai/glm-5.2", payload["notes"])
            self.assertIn("task_type=code-feature", payload["notes"])
            self.assertIn("retry=true", payload["notes"])

    def test_postgres_params_exclude_local_model_log_keys(self) -> None:
        class FakeConn:
            def __init__(self) -> None:
                self.params: dict[str, object] | None = None

            def execute(self, _sql: str, params: dict[str, object]) -> None:
                self.params = params

            def close(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as temp:
            logger = EvalLogger(
                EvalConfig(backend="jsonl", jsonl_path=Path(temp) / "eval.jsonl")
            )
            fake = FakeConn()
            logger._conn = fake
            row = {
                "run_id": "run",
                "pattern": "ringer-py",
                "task_key": "a",
                "spec": "spec",
                "worker_engine": "opencode",
                "shepherd_model": "gpt",
                "verify_method": "executed-check",
                "verdict": "PASS",
                "duration_ms": 1,
                "worker_tokens": 2,
                "notes": "retry=false",
                "orchestrator": "tester",
                "orch_task_id": "8e748b50-f5dc-48e8-bcb5-c193d4c95664",
                "proof_kinds": ["test"],
                "model": "openrouter/x",
                "task_type": "code-feature",
                "retry": False,
            }
            logger.log_attempt(row)
            self.assertIsNotNone(fake.params)
            assert fake.params is not None
            self.assertNotIn("model", fake.params)
            self.assertNotIn("task_type", fake.params)
            self.assertNotIn("retry", fake.params)
            self.assertEqual(
                {
                    "run_id",
                    "pattern",
                    "task_key",
                    "spec",
                    "worker_engine",
                    "shepherd_model",
                    "verify_method",
                    "verdict",
                    "duration_ms",
                    "worker_tokens",
                    "notes",
                    "orchestrator",
                    "orch_task_id",
                    "proof_kinds",
                },
                set(fake.params),
            )

    def test_models_aggregation_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "eval.jsonl"
            rows = [
                {
                    "run_id": "run1",
                    "task_key": "a",
                    "worker_engine": "opencode",
                    "model": "openrouter/x",
                    "task_type": "code-feature",
                    "verdict": "FAIL",
                    "duration_ms": 100,
                    "worker_tokens": 10,
                    "retry": False,
                    "logged_at": "2026-07-01T10:00:00+00:00",
                },
                {
                    "run_id": "run1",
                    "task_key": "a",
                    "worker_engine": "opencode",
                    "model": "openrouter/x",
                    "task_type": "code-feature",
                    "verdict": "PASS",
                    "duration_ms": 200,
                    "worker_tokens": 20,
                    "retry": True,
                    "logged_at": "2026-07-01T10:01:00+00:00",
                },
                {
                    "run_id": "run2",
                    "task_key": "b",
                    "worker_engine": "opencode",
                    "model": "openrouter/x",
                    "task_type": "code-feature",
                    "verdict": "PASS",
                    "duration_ms": 100,
                    "worker_tokens": 30,
                    "logged_at": "2026-07-03T10:00:00+00:00",
                },
                {
                    "run_id": "run3",
                    "task_key": "c",
                    "worker_engine": "codex",
                    "verdict": "FAIL",
                    "duration_ms": 50,
                    "worker_tokens": None,
                    "logged_at": "2026-06-30T10:00:00+00:00",
                },
                {
                    "run_id": "run4",
                    "task_key": "d",
                    "worker_engine": "opencode",
                    "model": "",
                    "task_type": "",
                    "verdict": "PASS",
                    "duration_ms": 80,
                    "worker_tokens": 5,
                    "notes": "retry=true",
                    "logged_at": "2026-07-04T10:00:00+00:00",
                },
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\nnot json\n",
                encoding="utf-8",
            )

            read_rows, skipped = read_model_log_rows(path, since="2026-07-01")
            self.assertEqual(4, len(read_rows))
            self.assertEqual(1, skipped)
            self.assertTrue(model_log_row_is_retry(read_rows[-1]))

            groups = aggregate_model_log_rows(read_rows)
            by_key = {(group["model"], group["task_type"]): group for group in groups}
            code = by_key[("openrouter/x", "code-feature")]
            self.assertEqual(2, code["tasks"])
            self.assertEqual(3, code["attempts"])
            self.assertEqual(2, code["passed"])
            self.assertEqual(0, code["failed"])
            self.assertEqual(1.0, code["pass_rate"])
            self.assertEqual(0.5, code["first_try_pass_rate"])
            self.assertEqual(150, code["median_duration_ms"])
            self.assertEqual(20, code["median_tokens"])
            self.assertEqual("2026-07-03T10:00:00+00:00", code["last_seen"])

            untyped = aggregate_model_log_rows(
                read_rows,
                model="opencode",
                task_type="(untyped)",
            )
            self.assertEqual(1, len(untyped))
            self.assertEqual("opencode", untyped[0]["model"])
            self.assertEqual("(untyped)", untyped[0]["task_type"])
            self.assertEqual(1, untyped[0]["tasks"])

    def test_since_selects_tasks_by_final_attempt_and_keeps_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "eval.jsonl"
            rows = [
                {
                    "run_id": "rescue-run",
                    "task_key": "task-a",
                    "worker_engine": "opencode",
                    "model": "openrouter/rescue",
                    "task_type": "code-feature",
                    "verdict": "FAIL",
                    "duration_ms": 100,
                    "worker_tokens": 10,
                    "retry": False,
                    "logged_at": "2026-07-01T23:59:00+00:00",
                },
                {
                    "run_id": "rescue-run",
                    "task_key": "task-a",
                    "worker_engine": "opencode",
                    "model": "openrouter/rescue",
                    "task_type": "code-feature",
                    "verdict": "PASS",
                    "duration_ms": 200,
                    "worker_tokens": 20,
                    "retry": True,
                    "logged_at": "2026-07-02T00:01:00+00:00",
                },
                {
                    "run_id": "old-run",
                    "task_key": "task-b",
                    "worker_engine": "opencode",
                    "model": "openrouter/rescue",
                    "task_type": "code-feature",
                    "verdict": "PASS",
                    "duration_ms": 300,
                    "worker_tokens": 30,
                    "retry": False,
                    "logged_at": "2026-07-01T20:00:00+00:00",
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            read_rows, skipped = read_model_log_rows(path, since="2026-07-02")

            self.assertEqual(0, skipped)
            self.assertEqual(["rescue-run", "rescue-run"], [row["run_id"] for row in read_rows])
            groups = aggregate_model_log_rows(read_rows)
            self.assertEqual(1, len(groups))
            group = groups[0]
            self.assertEqual(1, group["tasks"])
            self.assertEqual(2, group["attempts"])
            self.assertEqual(0.0, group["first_try_pass_rate"])
            self.assertEqual(1.0, group["pass_rate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
