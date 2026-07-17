#!/usr/bin/env python3
from __future__ import annotations

import json
import contextlib
import io
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402

from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    Manifest,
    RingerRunner,
    StateWriter,
    TaskRuntime,
    TaskSpec,
    VerifyResult,
    WorkerResult,
    lint_manifest,
)


LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)
GOOD_CHECK = (
    "test -s output.txt && grep -q 'ready' output.txt || "
    "{ echo 'FAIL: output.txt missing or does not contain ready'; exit 1; }"
)


class ContractMetadataTests(unittest.TestCase):
    def task(
        self,
        key: str = "core-contract-metadata",
        *,
        spec: str = LONG_SPEC,
        check: str = GOOD_CHECK,
        expect_files: list[str] | None = None,
        engine: str = "codex",
        model: str = "gpt-5.4-mini",
        task_type: str = "code-feature",
        verified: str = "the output file exists and contains the expected content",
        timeout_s: int = 1800,
        paperclip_issue: str = "JAC-3643",
        bead_id: str = "notes-hbq1q",
    ) -> dict[str, object]:
        return {
            "key": key,
            "spec": spec,
            "check": check,
            "engine": engine,
            "model": model,
            "task_type": task_type,
            "expect_files": ["output.txt"] if expect_files is None else expect_files,
            "verified": verified,
            "timeout_s": timeout_s,
            "paperclip_issue": paperclip_issue,
            "bead_id": bead_id,
        }

    def manifest_obj(
        self,
        *,
        workdir: Path,
        max_parallel: int = 2,
        risk: str | None = None,
        orchestrator: dict[str, object] | None = None,
        contract_review: dict[str, object] | None = None,
        tasks: list[dict[str, object]] | None = None,
        extra_top_level: list[tuple[str, object]] | None = None,
    ) -> dict[str, object]:
        manifest: dict[str, object] = {
            "run_name": "core-contract-metadata",
            "workdir": str(workdir),
            "max_parallel": max_parallel,
            "tasks": [self.task()] if tasks is None else tasks,
        }
        if risk is not None:
            manifest["risk"] = risk
        if orchestrator is not None:
            manifest["orchestrator"] = orchestrator
        if contract_review is not None:
            manifest["contract_review"] = contract_review
        if extra_top_level is not None:
            for key, value in extra_top_level:
                manifest[key] = value
        return manifest

    def manifest_from_obj(
        self,
        *,
        workdir: Path,
        max_parallel: int = 2,
        risk: str | None = None,
        orchestrator: dict[str, object] | None = None,
        contract_review: dict[str, object] | None = None,
        tasks: list[dict[str, object]] | None = None,
    ) -> Manifest:
        return Manifest.from_obj(
            self.manifest_obj(
                workdir=workdir,
                max_parallel=max_parallel,
                risk=risk,
                orchestrator=orchestrator,
                contract_review=contract_review,
                tasks=tasks,
            )
        )

    def write_manifest(self, path: Path, obj: dict[str, object]) -> None:
        path.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    def review(self, *, verdict: str = "PASS", family: str = "claude-4.8") -> dict[str, object]:
        return {
            "verdict": verdict,
            "reviewer_provider": "anthropic",
            "reviewer_model": "claude-opus-4-8",
            "reviewer_family": family,
            "reviewer_runtime": "claude-cli-2.1.196",
            "harness_session_id": "56e14e58-98bf-4ebb-9e02-28932300e169",
        }

    def build_app_config(self, root: Path) -> AppConfig:
        return AppConfig(
            path=None,
            identity_default=None,
            state_dir=root / "state",
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=root / "eval.jsonl"),
            engines={
                "codex": EngineConfig(
                    name="codex",
                    bin="/usr/local/bin/codex",
                    args_template=(
                        "exec",
                        "--skip-git-repo-check",
                        "{access_args}",
                        "{engine_args}",
                        "-C",
                        "{taskdir}",
                        "{spec}",
                    ),
                    full_access_args=("--dangerously-bypass-approvals-and-sandbox",),
                    sandbox_args=("--sandbox", "workspace-write"),
                    token_regex=None,
                    model_default="gpt-5.4-mini",
                )
            },
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(root / "live.html"),
                report_template=str(root / "report.html"),
                index_out=root / "index.html",
            ),
        )

    def compliant_manifest(self, *, workdir: Path, risk: str = "low") -> Manifest:
        return self.manifest_from_obj(
            workdir=workdir,
            risk=risk,
            tasks=[self.task(expect_files=["output.txt"])],
        )

    def test_old_manifests_remain_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.compliant_manifest(workdir=root / "work")

        self.assertEqual("low", manifest.risk)
        self.assertIsNone(manifest.orchestrator)
        self.assertIsNone(manifest.contract_review)
        self.assertEqual([], [item for item in lint_manifest(manifest) if "contract review" in item.lower()])

    def test_explicit_null_or_empty_risk_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = self.manifest_obj(workdir=root / "work")
            for value in (None, "", "   "):
                candidate = {**base, "risk": value}
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "risk"):
                    Manifest.from_obj(candidate)

    def test_from_path_and_with_max_parallel_preserve_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "manifest.json"
            manifest_obj = self.manifest_obj(
                workdir=root / "one",
                risk="High",
                orchestrator={
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "family": "gpt-5.6",
                    "runtime": "openai-codex",
                },
                contract_review=self.review(),
                extra_top_level=[
                    ("paperclip_issue", "JAC-3643"),
                    ("bead_id", "notes-hbq1q"),
                    ("routine_id", "routine-ignored"),
                    ("goal_id", "goal-ignored"),
                    ("timeout", 900),
                ],
            )
            self.write_manifest(path, manifest_obj)

            manifest = Manifest.from_path(path)
            with_max_parallel = manifest.with_max_parallel(4)

        self.assertEqual("high", manifest.risk)
        self.assertEqual(manifest_obj["orchestrator"], manifest.orchestrator)
        self.assertEqual(manifest_obj["contract_review"], manifest.contract_review)
        self.assertEqual(path, manifest.source_path)
        self.assertEqual(4, with_max_parallel.max_parallel)
        self.assertEqual(manifest.risk, with_max_parallel.risk)
        self.assertEqual(manifest.orchestrator, with_max_parallel.orchestrator)
        self.assertEqual(manifest.contract_review, with_max_parallel.contract_review)
        self.assertEqual(manifest.contract_sha256, with_max_parallel.contract_sha256)
        self.assertEqual(path, with_max_parallel.source_path)

    def test_contract_hash_ignores_irrelevant_metadata_and_key_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = [
                self.task(
                    spec=LONG_SPEC,
                    check=GOOD_CHECK,
                    expect_files=["output.txt"],
                    timeout_s=60,
                    paperclip_issue="JAC-0001",
                    bead_id="bead-a",
                )
            ]
            manifest_a = self.manifest_obj(
                workdir=root / "a",
                risk="critical",
                orchestrator={
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "family": "gpt-5.6",
                    "runtime": "openai-codex",
                    "attestation": "ignored",
                },
                contract_review={
                    "verdict": "PASS",
                    "reviewer_model": "claude-opus-4-8",
                    "reviewer_family": "claude-4.8",
                    "reviewer_runtime": "claude-cli-2.1.196",
                    "contract_sha256": "ignored",
                    "artifact": "ignored",
                },
                tasks=tasks,
                extra_top_level=[
                    ("paperclip_issue", "JAC-3643"),
                    ("bead_id", "notes-hbq1q"),
                    ("routine_id", "routine-a"),
                    ("goal_id", "goal-a"),
                    ("timeout", 30),
                ],
            )
            manifest_b = self.manifest_obj(
                workdir=root / "b",
                risk="critical",
                orchestrator={
                    "runtime": "openai-codex",
                    "family": "gpt-5.6",
                    "model": "gpt-5.6-sol",
                    "provider": "openai",
                    "attestation": "different",
                },
                contract_review={
                    "artifact": "different",
                    "contract_sha256": "different",
                    "reviewer_runtime": "claude-cli-2.1.196",
                    "reviewer_family": "claude-4.8",
                    "reviewer_model": "claude-opus-4-8",
                    "verdict": "PASS",
                },
                tasks=[
                    self.task(
                        spec=LONG_SPEC,
                        check=GOOD_CHECK,
                        expect_files=["output.txt"],
                        timeout_s=120,
                        paperclip_issue="JAC-9999",
                        bead_id="bead-b",
                    )
                ],
                extra_top_level=[
                    ("goal_id", "goal-b"),
                    ("timeout", 999),
                    ("routine_id", "routine-b"),
                    ("bead_id", "ignored"),
                    ("paperclip_issue", "ignored"),
                ],
            )
            path_a = root / "manifest-a.json"
            path_b = root / "manifest-b.json"
            self.write_manifest(path_a, manifest_a)
            self.write_manifest(path_b, manifest_b)

            loaded_a = Manifest.from_path(path_a)
            loaded_b = Manifest.from_path(path_b)

        self.assertEqual(loaded_a.contract_sha256, loaded_b.contract_sha256)
        self.assertEqual("critical", loaded_a.risk)
        self.assertEqual("critical", loaded_b.risk)

    def test_contract_hash_changes_with_semantic_task_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = self.manifest_from_obj(workdir=root / "base")
            changed = self.manifest_from_obj(
                workdir=root / "changed",
                tasks=[
                    self.task(
                        spec=LONG_SPEC + " Also write a tiny changelog entry.",
                        expect_files=["output.txt"],
                    )
                ],
            )

        self.assertNotEqual(base.contract_sha256, changed.contract_sha256)

    def test_low_and_medium_runs_do_not_require_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            low = self.compliant_manifest(workdir=root / "low", risk="low")
            medium = self.compliant_manifest(workdir=root / "medium", risk="medium")

        self.assertEqual([], [item for item in lint_manifest(low) if "contract review" in item.lower()])
        self.assertEqual([], [item for item in lint_manifest(medium) if "contract review" in item.lower()])

    def test_high_risk_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.compliant_manifest(workdir=root / "work", risk="high")

        findings = lint_manifest(manifest)
        self.assertTrue(any("contract review" in item.lower() for item in findings), findings)

    def test_high_risk_review_hash_mismatch_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = self.compliant_manifest(workdir=root / "work", risk="high")
            manifest = self.manifest_from_obj(
                workdir=root / "work",
                risk="high",
                orchestrator={
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "family": "gpt-5.6",
                    "runtime": "openai-codex",
                },
                contract_review={
                    **self.review(),
                    "contract_sha256": "deadbeef",
                },
                tasks=[self.task(expect_files=["output.txt"])],
            )

        self.assertEqual(base.contract_sha256, manifest.contract_sha256)
        findings = lint_manifest(manifest)
        self.assertTrue(any("contract_sha256" in item.lower() for item in findings), findings)

    def test_high_risk_same_family_review_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = self.manifest_from_obj(
                workdir=root / "work",
                risk="high",
                orchestrator={
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "family": "claude-4.8",
                    "runtime": "openai-codex",
                },
                tasks=[self.task(expect_files=["output.txt"])],
            )
            manifest = self.manifest_from_obj(
                workdir=root / "work",
                risk="high",
                orchestrator={
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "family": "claude-4.8",
                    "runtime": "openai-codex",
                },
                contract_review={
                    **self.review(family="claude-4.8"),
                    "contract_sha256": base.contract_sha256,
                },
                tasks=[self.task(expect_files=["output.txt"])],
            )

        findings = lint_manifest(manifest)
        self.assertTrue(any("same-family" in item.lower() for item in findings), findings)

    def test_high_risk_review_requires_harness_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = self.manifest_from_obj(
                workdir=root / "work",
                risk="high",
                orchestrator={"family": "gpt-5.6"},
                tasks=[self.task(expect_files=["output.txt"])],
            )
            review = self.review()
            review.pop("harness_session_id")
            manifest = self.manifest_from_obj(
                workdir=root / "work",
                risk="high",
                orchestrator={"family": "gpt-5.6"},
                contract_review={**review, "contract_sha256": base.contract_sha256},
                tasks=[self.task(expect_files=["output.txt"])],
            )

        findings = lint_manifest(manifest)
        self.assertTrue(any("harness_attestation" in item for item in findings), findings)

    def test_high_risk_family_comparison_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = self.manifest_from_obj(
                workdir=root / "work",
                risk="high",
                orchestrator={"family": "GPT-5.6"},
                tasks=[self.task(expect_files=["output.txt"])],
            )
            manifest = self.manifest_from_obj(
                workdir=root / "work",
                risk="high",
                orchestrator={"family": "GPT-5.6"},
                contract_review={
                    **self.review(family="gpt-5.6"),
                    "contract_sha256": base.contract_sha256,
                },
                tasks=[self.task(expect_files=["output.txt"])],
            )

        findings = lint_manifest(manifest)
        self.assertTrue(any("same-family" in item.lower() for item in findings), findings)

    def test_high_risk_cross_family_pass_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = self.manifest_from_obj(
                workdir=root / "work",
                risk="critical",
                orchestrator={
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "family": "gpt-5.6",
                    "runtime": "openai-codex",
                },
                tasks=[self.task(expect_files=["output.txt"])],
            )
            manifest = self.manifest_from_obj(
                workdir=root / "work",
                risk="critical",
                orchestrator={
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "family": "gpt-5.6",
                    "runtime": "openai-codex",
                },
                contract_review={
                    "verdict": "PASS",
                    "reviewer_provider": "anthropic",
                    "reviewer_model": "claude-opus-4-8",
                    "reviewer_family": "claude-4.8",
                    "reviewer_runtime": "claude-cli-2.1.196",
                    "harness_session_id": "56e14e58-98bf-4ebb-9e02-28932300e169",
                    "contract_sha256": base.contract_sha256,
                    "artifact": "review-artifact.json",
                },
                tasks=[self.task(expect_files=["output.txt"])],
            )

        self.assertEqual([], lint_manifest(manifest))

    def test_state_snapshots_expose_contract_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = self.manifest_from_obj(
                workdir=root / "work",
                risk="high",
                orchestrator={
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "family": "gpt-5.6",
                    "runtime": "openai-codex",
                },
                tasks=[self.task(expect_files=["output.txt"])],
            )
            manifest = Manifest.from_obj(
                self.manifest_obj(
                    workdir=root / "work",
                    risk="high",
                    orchestrator={
                        "provider": "openai",
                        "model": "gpt-5.6-sol",
                        "family": "gpt-5.6",
                        "runtime": "openai-codex",
                    },
                    contract_review={
                        "verdict": "PASS",
                        "reviewer_provider": "anthropic",
                        "reviewer_model": "claude-opus-4-8",
                        "reviewer_family": "claude-4.8",
                        "reviewer_runtime": "claude-cli-2.1.196",
                        "harness_session_id": "56e14e58-98bf-4ebb-9e02-28932300e169",
                        "contract_sha256": base.contract_sha256,
                    },
                    tasks=[self.task(expect_files=["output.txt"])],
                )
            )
            runner = RingerRunner(
                manifest,
                config=self.build_app_config(root),
                identity="tester",
                dashboard_enabled=False,
            )
            runtime = runner.runtimes[0]
            runtime.status = "pass"
            runtime.final_verdict = "PASS"
            runtime.ended_at_monotonic = 2.0

            live_state = runner.state_writer.flush()
            runner.state_writer.finish()
            final_state = json.loads(runner.state_writer.path.read_text(encoding="utf-8"))

        for state in (live_state, final_state):
            self.assertEqual("high", state["risk"])
            self.assertEqual(manifest.orchestrator, state["orchestrator"])
            self.assertEqual(manifest.contract_review, state["contract_review"])
            self.assertEqual(manifest.contract_sha256, state["contract_sha256"])
        self.assertEqual("finished", final_state["state"])

    def test_attempt_evidence_includes_contract_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = self.manifest_from_obj(
                workdir=root / "work",
                risk="high",
                orchestrator={
                    "provider": "openai",
                    "model": "gpt-5.6-sol",
                    "family": "gpt-5.6",
                    "runtime": "openai-codex",
                },
                tasks=[self.task(expect_files=["output.txt"])],
            )
            manifest = Manifest.from_obj(
                self.manifest_obj(
                    workdir=root / "work",
                    risk="high",
                    orchestrator={
                        "provider": "openai",
                        "model": "gpt-5.6-sol",
                        "family": "gpt-5.6",
                        "runtime": "openai-codex",
                    },
                    contract_review={
                        "verdict": "PASS",
                        "reviewer_provider": "anthropic",
                        "reviewer_model": "claude-opus-4-8",
                        "reviewer_family": "claude-4.8",
                        "reviewer_runtime": "claude-cli-2.1.196",
                        "harness_session_id": "56e14e58-98bf-4ebb-9e02-28932300e169",
                        "contract_sha256": base.contract_sha256,
                    },
                    tasks=[self.task(expect_files=["output.txt"])],
                )
            )
            runner = RingerRunner(
                manifest,
                config=self.build_app_config(root),
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

        self.assertEqual("high", payload["risk"])
        self.assertEqual(manifest.contract_sha256, payload["contract_sha256"])
        self.assertEqual("gpt-5.6-sol", payload["orchestrator_model"])
        self.assertEqual("openai-codex", payload["orchestrator_runtime"])
        self.assertEqual("claude-opus-4-8", payload["reviewer_model"])
        self.assertEqual("claude-cli-2.1.196", payload["reviewer_runtime"])
        self.assertEqual("PASS", payload["reviewer_verdict"])
        self.assertIn("contract_sha256=", payload["notes"])
        self.assertIn("orchestrator_model=gpt-5.6-sol", payload["notes"])
        self.assertIn("reviewer_model=claude-opus-4-8", payload["notes"])
        self.assertIn("reviewer_verdict=PASS", payload["notes"])

    def test_run_blocks_high_risk_before_dry_run_or_worker_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "manifest.json"
            self.write_manifest(
                path,
                self.manifest_obj(
                    workdir=root / "work",
                    risk="high",
                    orchestrator={"family": "gpt-5.6"},
                    tasks=[self.task(expect_files=["output.txt"])],
                ),
            )
            config = self.build_app_config(root)
            output = io.StringIO()
            with (
                mock.patch.object(ringer.AppConfig, "load", return_value=config),
                mock.patch.object(ringer, "dry_run") as dry_run_mock,
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(output),
            ):
                exit_code = ringer.main(
                    ["run", str(path), "--dry-run", "--no-dashboard"]
                )

        self.assertEqual(1, exit_code)
        self.assertIn("dispatch blocked", output.getvalue())
        dry_run_mock.assert_not_called()

    def test_runner_reasserts_high_risk_gate_for_programmatic_callers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.manifest_from_obj(
                workdir=root / "work",
                risk="critical",
                orchestrator={"family": "gpt-5.6"},
                tasks=[self.task(expect_files=["output.txt"])],
            )

            with self.assertRaisesRegex(ValueError, "dispatch blocked"):
                RingerRunner(
                    manifest,
                    config=self.build_app_config(root),
                    identity="programmatic-caller",
                    dashboard_enabled=False,
                )

            self.assertFalse((root / "state").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
