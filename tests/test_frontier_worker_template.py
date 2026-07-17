from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "nate-native-fleet-policy.md"
SCHEMA = ROOT / "schema" / "ringer-contract-review.v1.json"
TEMPLATE_README = ROOT / "templates" / "frontier-worker-loop" / "README.md"
TEMPLATE_MANIFEST = ROOT / "templates" / "frontier-worker-loop" / "manifest.json"

sys.path.insert(0, str(ROOT))

from ringer import Manifest, lint_manifest  # noqa: E402


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_policy_defines_the_corrected_control_loop_and_modes() -> None:
    text = POLICY.read_text(encoding="utf-8")
    lower_text = text.lower()
    required_phrases = [
        "frontier orchestrator writes the immutable contract and executable checks",
        "economical proven workers implement in isolated parallel lanes",
        "executable checks decide pass/fail",
        "exactly one retry receives raw failure output",
        "receipts drive per-task-type routing",
        "semantic review is conditional and can veto green but never override red",
        "ringside is observational",
        "paperclip is trigger/telemetry/work-product projection",
        "beads/vault remain authoritative",
        "canonical",
        "contract-reviewed high-risk",
        "semantic-review",
        "bakeoff/exploration",
        "80-90 percent proven / 10-20 percent exploration",
        "roughly 20 comparable receipts",
    ]
    for phrase in required_phrases:
        assert phrase in lower_text, phrase


def test_contract_review_schema_is_strict_and_has_the_required_receipt_fields() -> None:
    schema = load_json(SCHEMA)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    required = schema["required"]
    for field in [
        "receipt_version",
        "receipt_id",
        "verdict",
        "contract_sha256",
        "reviewed_at",
        "reviewer",
        "harness_attestation",
        "requirement_gaps",
        "required_changes",
        "advisories",
    ]:
        assert field in required
    assert schema["properties"]["verdict"]["enum"] == ["PASS", "FAIL"]
    assert schema["properties"]["contract_sha256"]["pattern"] == "^[0-9a-f]{64}$"
    reviewer = schema["properties"]["reviewer"]
    assert reviewer["type"] == "object"
    assert reviewer["additionalProperties"] is False
    assert reviewer["required"] == ["provider", "model", "family", "runtime"]
    for field in ["provider", "model", "family", "runtime"]:
        assert reviewer["properties"][field]["type"] == "string"
    assert "if" in schema and "then" in schema and "else" in schema


def test_frontier_worker_manifest_contains_the_required_lane_structure() -> None:
    manifest = load_json(TEMPLATE_MANIFEST)
    assert manifest["template_unfilled"] is True
    assert manifest["risk"] == "high"
    orchestrator = manifest["orchestrator"]
    assert orchestrator["provider"] == "{{ORCHESTRATOR_PROVIDER}}"
    assert orchestrator["model"] == "{{ORCHESTRATOR_MODEL}}"
    assert orchestrator["family"] == "{{ORCHESTRATOR_FAMILY}}"
    assert orchestrator["runtime"] == "{{ORCHESTRATOR_RUNTIME}}"
    contract_review = manifest["contract_review"]
    assert contract_review["contract_sha256"] == "{{CONTRACT_SHA256}}"
    assert contract_review["verdict"] == "PASS"
    assert contract_review["reviewer_model"] == "{{REVIEWER_MODEL}}"
    assert contract_review["reviewer_family"] == "{{REVIEWER_FAMILY}}"
    assert contract_review["reviewer_runtime"] == "{{REVIEWER_RUNTIME}}"
    assert contract_review["harness_attestation"] == "{{HARNESS_ATTESTATION}}"
    assert contract_review["cross_family_required"] is True
    assert "not dispatchable before exact-manifest cross-family PASS" in contract_review["dispatch_gate"]
    tasks = manifest["tasks"]
    assert len(tasks) == 2
    assert manifest["max_parallel"] == 2
    keys = {task["key"] for task in tasks}
    assert keys == {"policy-lane", "template-lane"}
    for task in tasks:
        assert task["task_type"]
        assert task["engine"] == "codex"
        assert task["model"]
        assert task["paperclip_issue"].startswith("{{")
        assert task["bead_id"].startswith("{{")
        assert task["spec"]
        assert task["check"]
        assert task["verified"]
        assert "print(" in task["check"]
        assert "raise SystemExit(" in task["check"]
        assert "FAIL:" in task["check"]
    assert "policy.md" in tasks[0]["expect_files"] or "policy.md" in tasks[1]["expect_files"]
    assert "template.md" in tasks[0]["expect_files"] or "template.md" in tasks[1]["expect_files"]


def test_frontier_worker_checks_really_pass_and_fail() -> None:
    manifest = load_json(TEMPLATE_MANIFEST)
    passing_content = {
        "policy-lane": "\n".join(
            [
                "frontier orchestrator writes the immutable contract and executable checks",
                "economical proven workers implement in isolated parallel lanes",
                "executable checks decide PASS/FAIL",
                "exactly one retry receives raw failure output",
                "receipts drive per-task-type routing",
                "semantic review is conditional and can veto green but never override red",
                "Ringside is observational",
                "Paperclip is trigger/telemetry/work-product projection",
                "Beads/Vault remain authoritative",
            ]
        ),
        "template-lane": (
            "A frontier orchestrator owns fill freeze hash review lint run integrate "
            "semantic-review receipt."
        ),
    }
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for task in manifest["tasks"]:
            output = root / task["expect_files"][0]
            output.write_text(passing_content[task["key"]], encoding="utf-8")
            passed = subprocess.run(
                task["check"],
                cwd=root,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )
            assert passed.returncode == 0, passed.stdout + passed.stderr
            assert "PASS:" in passed.stdout

            output.write_text("incomplete", encoding="utf-8")
            failed = subprocess.run(
                task["check"],
                cwd=root,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )
            assert failed.returncode != 0
            assert "FAIL:" in failed.stdout


def test_filled_template_matches_core_contract_review_gate() -> None:
    template = TEMPLATE_MANIFEST.read_text(encoding="utf-8")
    replacements = {
        "{{RUN_SLUG}}": "frontier-worker-template-test",
        "{{WORKDIR}}": "/tmp/frontier-worker-template-test",
        "{{ORCHESTRATOR_PROVIDER}}": "openai",
        "{{ORCHESTRATOR_MODEL}}": "gpt-5.6-sol",
        "{{ORCHESTRATOR_FAMILY}}": "gpt-5.6",
        "{{ORCHESTRATOR_RUNTIME}}": "openai-codex",
        "{{ORCHESTRATOR_ATTESTATION}}": "session-sol-123",
        "{{REVIEWER_PROVIDER}}": "anthropic",
        "{{REVIEWER_MODEL}}": "claude-opus-4-8",
        "{{REVIEWER_FAMILY}}": "claude-4.8",
        "{{REVIEWER_RUNTIME}}": "claude-cli-2.1.196",
        "{{HARNESS_ATTESTATION}}": "session-opus-456",
        "{{CONTRACT_REVIEW_RECEIPT_PATH}}": "review-receipt.json",
        "{{CONTRACT_SHA256}}": "0" * 64,
        "{{ECONOMICAL_MODEL_POLICY}}": "gpt-5.4-mini",
        "{{ECONOMICAL_MODEL_TEMPLATE}}": "gpt-5.4-mini",
        "{{PAPERCLIP_ISSUE_POLICY}}": "JAC-3643",
        "{{PAPERCLIP_ISSUE_TEMPLATE}}": "JAC-3643",
        "{{BEAD_ID_POLICY}}": "notes-hbq1q",
        "{{BEAD_ID_TEMPLATE}}": "notes-hbq1q",
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    obj = json.loads(template)
    provisional = Manifest.from_obj(obj)
    obj["contract_review"]["contract_sha256"] = provisional.contract_sha256
    manifest = Manifest.from_obj(obj)

    assert lint_manifest(manifest) == []


def test_template_readme_makes_the_authority_boundaries_and_workflow_explicit() -> None:
    text = TEMPLATE_README.read_text(encoding="utf-8")
    for phrase in [
        "Fill the placeholders",
        "Freeze the contract text",
        "Hash the exact manifest contract",
        "Review the frozen contract",
        "Lint the manifest",
        "Run the worker lanes",
        "Integrate only after the executable checks PASS",
        "semantic-review",
        "recording the receipt",
        "Frontier orchestrator",
        "Economical proven workers",
        "Ringside: observational",
        "Paperclip: trigger/telemetry/work-product projection only",
        "Beads and Vault: authoritative lifecycle and knowledge sources",
    ]:
        assert phrase in text, phrase
