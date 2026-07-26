#!/usr/bin/env python3
"""Launch-receipt tests: schema conformance, adversarial secret rejection,
append-only lifecycle with latest-line-wins, and the FLEET_* child env contract.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    ReceiptSecurityError,
    ReceiptWriter,
    build_launch_receipt,
    build_terminal_receipt,
    fleet_child_env,
    new_receipt_id,
    scan_receipt_material,
)


def load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_launch_receipts", ROOT / "tools" / "verify_launch_receipts.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = load_verifier()
SCHEMA = json.loads((ROOT / "schema" / "launch-receipt.v1.json").read_text(encoding="utf-8"))

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional cross-check only
    jsonschema = None


def sample_launch_receipt(receipt_id: str | None = None, **overrides) -> dict:
    kwargs = dict(
        identity="aegis-ringer",
        run_id="demo-20260711T000000Z-p1",
        host="aegis",
        entrypoint="codex",
        model="gpt-5",
        cwd="/tmp/ringer-task",
        full_access=False,
        spec="Write hello to output.txt and verify it round-trips.",
        intent="ringer run demo task t1 attempt 1",
        bead_id="hermes-x50",
        parent_session=None,
    )
    kwargs.update(overrides)
    return build_launch_receipt(receipt_id or new_receipt_id(), **kwargs)


class ReceiptIdTests(unittest.TestCase):
    def test_receipt_id_shape_and_uniqueness(self) -> None:
        ids = {new_receipt_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)
        for receipt_id in ids:
            self.assertRegex(receipt_id, r"^lr-[0-9A-HJKMNP-TV-Z]{26}$")


class SchemaConformanceTests(unittest.TestCase):
    def test_launched_receipt_passes_verifier_validation(self) -> None:
        receipt = sample_launch_receipt()
        self.assertEqual(VERIFIER.validate_receipt(receipt), [])

    def test_terminal_receipt_passes_verifier_validation(self) -> None:
        receipt = build_terminal_receipt(
            new_receipt_id(),
            "completed",
            identity="aegis-ringer",
            run_id="demo-20260711T000000Z-p1",
            host="aegis",
        )
        self.assertEqual(VERIFIER.validate_receipt(receipt), [])

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_receipts_validate_against_schema_file(self) -> None:
        jsonschema.validate(sample_launch_receipt(), SCHEMA)
        jsonschema.validate(
            build_terminal_receipt(
                new_receipt_id(),
                "failed",
                identity="aegis-ringer",
                run_id="demo-20260711T000000Z-p1",
                host="aegis",
            ),
            SCHEMA,
        )

    def test_unknown_fields_rejected(self) -> None:
        receipt = sample_launch_receipt()
        receipt["surprise"] = "field"
        errors = VERIFIER.validate_receipt(receipt)
        self.assertTrue(any("unknown field: surprise" in error for error in errors))
        if jsonschema is not None:
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(receipt, SCHEMA)

    def test_prompt_is_hashed_never_stored(self) -> None:
        spec = "Top secret spec text that must never appear in a receipt."
        receipt = sample_launch_receipt(spec=spec)
        line = json.dumps(receipt)
        self.assertNotIn("Top secret", line)
        self.assertRegex(receipt["launched"]["prompt_sha256"], r"^[0-9a-f]{64}$")


class SecretRejectionTests(unittest.TestCase):
    """Adversarial fixtures: the writer must reject, not store or rewrite."""

    FIXTURES = {
        "url-with-query-string": "callback http://127.0.0.1:28491/oauth/callback?code=abc123&state=xyz",
        "authorization-header": "Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ",
        "bearer-token": "bearer sk-ant-api03-abcdefghijklmnop",
        "oauth-code-param": "resume with code=4/0AVG7fiQ",
        "oauth-state-param": "state=af0ifjsldkj was returned",
        "jwt-shaped": "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4",
        "private-key-block": "-----BEGIN RSA PRIVATE KEY-----",
        "cookie-header": "Set-Cookie: session=deadbeef",
    }

    def test_each_fixture_rejected_in_every_free_text_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "launches.jsonl"
            writer = ReceiptWriter(path)
            for name, payload in self.FIXTURES.items():
                for field in ("intent", "identity", "entrypoint"):
                    receipt = sample_launch_receipt(**{field: payload[:120]})
                    with self.assertRaises(
                        ReceiptSecurityError, msg=f"fixture {name} in {field} not rejected"
                    ):
                        writer.emit(receipt)
                receipt = sample_launch_receipt()
                receipt["notes"] = payload[:180]
                with self.assertRaises(ReceiptSecurityError, msg=f"fixture {name} in notes"):
                    writer.emit(receipt)
            self.assertFalse(path.exists(), "rejected receipts must never touch disk")

    def test_rejection_message_never_echoes_the_secret(self) -> None:
        writer = ReceiptWriter(Path(tempfile.mkdtemp()) / "launches.jsonl")
        try:
            writer.emit(sample_launch_receipt(intent="Authorization: Bearer sk-live-abcdef123456"))
        except ReceiptSecurityError as exc:
            self.assertNotIn("sk-live", str(exc))
        else:
            self.fail("expected ReceiptSecurityError")

    def test_clean_output_matches_forbidden_patterns_zero_times(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "launches.jsonl"
            writer = ReceiptWriter(path)
            writer.emit(sample_launch_receipt())
            writer.emit(
                build_terminal_receipt(
                    new_receipt_id(),
                    "completed",
                    identity="aegis-ringer",
                    run_id="demo-20260711T000000Z-p1",
                    host="aegis",
                )
            )
            content = path.read_text(encoding="utf-8")
            self.assertEqual(scan_receipt_material(content), [])
            self.assertEqual(VERIFIER.scan_forbidden(content), [])


class LifecycleTests(unittest.TestCase):
    def test_append_only_and_latest_line_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "launches.jsonl"
            writer = ReceiptWriter(path)
            receipt = sample_launch_receipt()
            receipt_id = receipt["receipt_id"]
            writer.emit(receipt)
            first_snapshot = path.read_bytes()
            writer.emit(
                build_terminal_receipt(
                    receipt_id,
                    "completed",
                    identity="aegis-ringer",
                    run_id="demo-20260711T000000Z-p1",
                    host="aegis",
                )
            )
            content = path.read_bytes()
            self.assertTrue(
                content.startswith(first_snapshot),
                "existing lines must never be rewritten in place",
            )
            self.assertEqual(content.decode("utf-8").count("\n"), 2)

            records, violations = VERIFIER.load_receipt_lines(path)
            self.assertEqual(violations, [])
            resolved = VERIFIER.resolve_latest(records)
            self.assertEqual(resolved[receipt_id]["event"], "completed")
            self.assertEqual(resolved[receipt_id]["_first_event"], "launched")
            # launched/work sections from the first line carry forward.
            self.assertEqual(resolved[receipt_id]["work"]["bead_id"], "hermes-x50")

    def test_terminal_before_launched_is_a_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "launches.jsonl"
            ReceiptWriter(path).emit(
                build_terminal_receipt(
                    new_receipt_id(),
                    "completed",
                    identity="aegis-ringer",
                    run_id="demo-20260711T000000Z-p1",
                    host="aegis",
                )
            )
            exit_code = VERIFIER.main(["--receipts", str(path), "--no-projects"])
            self.assertEqual(exit_code, 1)

    def test_stale_open_receipt_reported_as_abandoned_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "launches.jsonl"
            receipt = sample_launch_receipt()
            stale_at = datetime.now(timezone.utc) - timedelta(hours=25)
            receipt["emitted_at"] = stale_at.isoformat(timespec="seconds").replace("+00:00", "Z")
            ReceiptWriter(path).emit(receipt)
            records, violations = VERIFIER.load_receipt_lines(path)
            self.assertEqual(violations, [])
            # Verifier is read-only: it reports the candidate but the file is untouched.
            before = path.read_bytes()
            exit_code = VERIFIER.main(["--receipts", str(path), "--no-projects"])
            self.assertEqual(exit_code, 0, "stale-open is a warning, not a violation")
            self.assertEqual(path.read_bytes(), before)

    def test_verifier_flags_unattributed_session_and_accepts_bound_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "launches.jsonl"
            projects = root / "projects" / "-some-project"
            projects.mkdir(parents=True)
            writer = ReceiptWriter(path)
            receipt = sample_launch_receipt()
            writer.emit(receipt)
            session_start = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            )
            (projects / "aaaa1111.jsonl").write_text(
                json.dumps(
                    {
                        "sessionId": "aaaa1111",
                        "userType": "external",
                        "entrypoint": "sdk-cli",
                        "timestamp": session_start,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            exit_code = VERIFIER.main(
                ["--receipts", str(path), "--projects", str(root / "projects")]
            )
            self.assertEqual(exit_code, 1, "post-cutover session without a receipt must flag")

            # A 'bound' line joining the session to a receipt clears the alarm.
            bound = build_terminal_receipt(
                receipt["receipt_id"],
                "completed",
                identity="aegis-ringer",
                run_id="demo-20260711T000000Z-p1",
                host="aegis",
            )
            bound["event"] = "bound"
            bound["launched"] = dict(receipt["launched"], session_id="aaaa1111", kind="claude-code-session")
            writer.emit(bound)
            exit_code = VERIFIER.main(
                ["--receipts", str(path), "--projects", str(root / "projects")]
            )
            self.assertEqual(exit_code, 0)


class ChildEnvContractTests(unittest.TestCase):
    def test_fleet_env_contract_fields(self) -> None:
        receipt_id = new_receipt_id()
        env = fleet_child_env(
            {"PATH": "/usr/bin"},
            identity="aegis-ringer",
            receipt_id=receipt_id,
            bead_id="hermes-x50",
            parent_session="98b92750-0000-0000-0000-000000000000",
        )
        self.assertEqual(env["FLEET_LAUNCHER"], "aegis-ringer")
        self.assertEqual(env["FLEET_LAUNCHER_KIND"], "orchestrator")
        self.assertEqual(env["FLEET_LAUNCH_ID"], receipt_id)
        self.assertEqual(env["FLEET_BEAD"], "hermes-x50")
        self.assertEqual(env["FLEET_PARENT_SESSION"], "98b92750-0000-0000-0000-000000000000")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_optional_fields_omitted_when_unknown(self) -> None:
        env = fleet_child_env({}, identity="aegis-ringer", receipt_id=new_receipt_id())
        self.assertNotIn("FLEET_BEAD", env)
        self.assertNotIn("FLEET_PARENT_SESSION", env)


if __name__ == "__main__":
    unittest.main()
