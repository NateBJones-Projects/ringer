#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    AGENTOPS_AGENT_ID,
    AGENTOPS_RUNTIME,
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    EvalLogger,
    Manifest,
    PostgresAgentOpsClient,
    RingerRunner,
)


TASK_ID = "8e748b50-f5dc-48e8-bcb5-c193d4c95664"


class FakeCursor:
    def __init__(self, result: Any = None) -> None:
        self.result = {"ok": True} if result is None else result

    def fetchone(self) -> tuple[Any]:
        return (self.result,)


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.closed = False

    def execute(self, sql: str, params: Any) -> FakeCursor:
        self.calls.append((sql, params))
        return FakeCursor()

    def close(self) -> None:
        self.closed = True


class FakeAgentOpsClient:
    def __init__(
        self,
        events: list[str],
        *,
        claim_error: Exception | None = None,
        submit_failures: int = 0,
    ) -> None:
        self.events = events
        self.claim_error = claim_error
        self.submit_failures = submit_failures
        self.claims: list[str] = []
        self.outcomes: list[dict[str, Any]] = []

    def claim(self, task_id: str) -> dict[str, bool]:
        self.events.append("claim")
        self.claims.append(task_id)
        if self.claim_error is not None:
            raise self.claim_error
        return {"ok": True}

    def submit_outcome(self, **kwargs: Any) -> dict[str, bool]:
        self.events.append("outcome")
        self.outcomes.append(kwargs)
        if self.submit_failures > 0:
            self.submit_failures -= 1
            raise RuntimeError("transient submit failure")
        return {"ok": True}


class AgentOpsLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def manifest_obj(
        self,
        *,
        autostart: bool = True,
        proof_kinds: list[str] | None = None,
    ) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "run_name": "agentops-test",
            "workdir": str(self.root / "work"),
            "max_parallel": 1,
            "worktrees": False,
            "orch_task_id": TASK_ID,
            "agentops_autostart": autostart,
            "tasks": [
                {
                    "key": "task-one",
                    "spec": "Perform the bounded task.",
                    "check": "true",
                    "engine": "mock",
                    "model": "",
                }
            ],
        }
        if proof_kinds is not None:
            obj["proof_kinds"] = proof_kinds
        return obj

    def config(self) -> AppConfig:
        state_dir = self.root / "state"
        return AppConfig(
            path=None,
            identity_default="test",
            state_dir=state_dir,
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=state_dir / "runs.jsonl"),
            engines={
                "mock": EngineConfig(
                    name="mock",
                    bin=sys.executable,
                    args_template=("-c", "pass"),
                    full_access_args=(),
                    sandbox_args=(),
                    token_regex=None,
                )
            },
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(state_dir / "artifacts" / "{run_id}.html"),
                report_template=str(state_dir / "artifacts" / "{run_id}-report.html"),
                index_out=state_dir / "artifacts" / "index.html",
            ),
        )

    def test_manifest_defaults_typed_proof_to_test(self) -> None:
        manifest = Manifest.from_obj(self.manifest_obj())
        self.assertEqual(("test",), manifest.proof_kinds)

    def test_manifest_rejects_autostart_without_existing_task_id(self) -> None:
        obj = self.manifest_obj()
        obj.pop("orch_task_id")
        with self.assertRaisesRegex(
            ValueError, "AgentOps autostart requires an existing orch_task_id"
        ):
            Manifest.from_obj(obj)

    def test_manifest_rejects_unknown_proof_kind(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported proof_kinds: vibes"):
            Manifest.from_obj(self.manifest_obj(proof_kinds=["test", "vibes"]))

    def test_manifest_rejects_native_proof_without_required_metadata(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "require explicit build/device metadata"
        ):
            Manifest.from_obj(
                self.manifest_obj(
                    proof_kinds=["native_distribution", "physical_acceptance"]
                )
            )

    def test_postgres_client_calls_three_argument_claim_contract(self) -> None:
        connection = FakeConnection()
        client = PostgresAgentOpsClient(connection)
        client.claim(TASK_ID)
        sql, params = connection.calls[0]
        self.assertIn("agentops_claim_work_v2", sql)
        self.assertEqual((TASK_ID, AGENTOPS_AGENT_ID, AGENTOPS_RUNTIME), params)

    def test_postgres_client_submits_typed_idempotent_outcome(self) -> None:
        connection = FakeConnection()
        client = PostgresAgentOpsClient(connection)
        proofs = [{"kind": "test", "status": "passed", "label": "Executed tests"}]
        client.submit_outcome(
            task_id=TASK_ID,
            summary="Passed",
            proofs=proofs,
            remaining_risks=[],
            receipt_run_ids=["run-1"],
            blocked=False,
            submission_key="ringer:key",
        )
        sql, params = connection.calls[0]
        self.assertIn("agentops_submit_outcome_v2", sql)
        self.assertEqual(TASK_ID, params[0])
        self.assertEqual(proofs, json.loads(params[2]))
        self.assertEqual(AGENTOPS_AGENT_ID, params[5])
        self.assertEqual(AGENTOPS_RUNTIME, params[6])
        self.assertFalse(params[7])
        self.assertEqual("ringer:key", params[8])

    def test_receipt_insert_carries_proof_kinds(self) -> None:
        config = EvalConfig(
            backend="jsonl", jsonl_path=self.root / "fallback.jsonl"
        )
        logger = EvalLogger(config)
        connection = FakeConnection()
        logger._conn = connection
        logger.log_attempt(
            {
                "run_id": "run-1",
                "pattern": "ringer-py",
                "task_key": "task-one",
                "spec": "spec",
                "worker_engine": "mock",
                "shepherd_model": "none",
                "verify_method": "executed-check",
                "verdict": "PASS",
                "duration_ms": 1,
                "worker_tokens": None,
                "notes": "",
                "orchestrator": "ringer",
                "orch_task_id": TASK_ID,
                "proof_kinds": ["test", "artifact"],
            }
        )
        sql, params = connection.calls[0]
        self.assertIn("orch_task_id, proof_kinds", sql)
        self.assertEqual(["test", "artifact"], params["proof_kinds"])

    def test_autostart_claims_before_workers_and_submits_once(self) -> None:
        events: list[str] = []
        client = FakeAgentOpsClient(events)
        runner = RingerRunner(
            Manifest.from_obj(
                self.manifest_obj(proof_kinds=["test", "artifact", "test"])
            ),
            config=self.config(),
            identity="local-identity",
            dashboard_enabled=False,
            agentops_client=client,
        )

        async def fake_run_task(runtime: Any) -> None:
            events.append("worker")
            runtime.attempts = 1
            runtime.status = "pass"
            runtime.final_verdict = "PASS"
            runtime.started_at_monotonic = time.monotonic()
            runtime.ended_at_monotonic = time.monotonic()
            runner._logged_attempts += 1

        runner._run_task = fake_run_task  # type: ignore[method-assign]
        exit_code = asyncio.run(runner.run())

        self.assertEqual(0, exit_code)
        self.assertEqual(["claim", "worker", "outcome"], events)
        self.assertEqual([TASK_ID], client.claims)
        self.assertEqual(1, len(client.outcomes))
        outcome = client.outcomes[0]
        self.assertFalse(outcome["blocked"])
        self.assertEqual([runner.run_id], outcome["receipt_run_ids"])
        self.assertEqual(
            ["test", "artifact"],
            [proof["kind"] for proof in outcome["proofs"]],
        )
        self.assertTrue(
            all("verified_at" not in proof for proof in outcome["proofs"]),
            "the database stamps receipt-backed verification time so retries keep the same payload",
        )
        self.assertTrue(
            str(outcome["submission_key"]).startswith(f"ringer:{TASK_ID}:")
        )

    def test_failed_claim_never_starts_workers_or_submits_outcome(self) -> None:
        events: list[str] = []
        client = FakeAgentOpsClient(events, claim_error=RuntimeError("not approved"))
        runner = RingerRunner(
            Manifest.from_obj(self.manifest_obj()),
            config=self.config(),
            identity="local-identity",
            dashboard_enabled=False,
            agentops_client=client,
        )

        async def fake_run_task(_runtime: Any) -> None:
            events.append("worker")

        runner._run_task = fake_run_task  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "not approved"):
            asyncio.run(runner.run())

        self.assertEqual(["claim"], events)
        self.assertEqual([], client.outcomes)

    def test_failed_checks_submit_one_blocked_outcome(self) -> None:
        events: list[str] = []
        client = FakeAgentOpsClient(events)
        runner = RingerRunner(
            Manifest.from_obj(self.manifest_obj(proof_kinds=["test"])),
            config=self.config(),
            identity="local-identity",
            dashboard_enabled=False,
            agentops_client=client,
        )

        async def fake_run_task(runtime: Any) -> None:
            events.append("worker")
            runtime.attempts = 2
            runtime.status = "fail"
            runtime.final_verdict = "FAIL"
            runtime.started_at_monotonic = time.monotonic()
            runtime.ended_at_monotonic = time.monotonic()
            runner._logged_attempts += 1

        runner._run_task = fake_run_task  # type: ignore[method-assign]
        exit_code = asyncio.run(runner.run())

        self.assertEqual(1, exit_code)
        self.assertEqual(["claim", "worker", "outcome"], events)
        self.assertEqual(1, len(client.outcomes))
        outcome = client.outcomes[0]
        self.assertTrue(outcome["blocked"])
        self.assertEqual("failed", outcome["proofs"][0]["status"])
        self.assertIn("task-one", outcome["remaining_risks"][0])

    def test_transient_outcome_failure_retries_same_idempotency_key(self) -> None:
        events: list[str] = []
        client = FakeAgentOpsClient(events, submit_failures=1)
        runner = RingerRunner(
            Manifest.from_obj(self.manifest_obj(proof_kinds=["test"])),
            config=self.config(),
            identity="local-identity",
            dashboard_enabled=False,
            agentops_client=client,
        )

        async def fake_run_task(runtime: Any) -> None:
            events.append("worker")
            runtime.attempts = 1
            runtime.status = "pass"
            runtime.final_verdict = "PASS"
            runtime.started_at_monotonic = time.monotonic()
            runtime.ended_at_monotonic = time.monotonic()
            runner._logged_attempts += 1

        runner._run_task = fake_run_task  # type: ignore[method-assign]
        exit_code = asyncio.run(runner.run())

        self.assertEqual(0, exit_code)
        self.assertEqual(["claim", "worker", "outcome", "outcome"], events)
        self.assertEqual(2, len(client.outcomes))
        self.assertEqual(
            client.outcomes[0]["submission_key"],
            client.outcomes[1]["submission_key"],
        )
        self.assertEqual(
            client.outcomes[0]["proofs"],
            client.outcomes[1]["proofs"],
        )
        self.assertFalse(client.outcomes[1]["blocked"])
        self.assertTrue(runner._agentops_outcome_attempted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
