#!/usr/bin/env python3
"""redact_secrets and its wiring into the check-output / log-tail funnels."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "scrub-run-logs.py"
sys.path.insert(0, str(ROOT))

from ringer import TaskSpec, Verifier, redact_secrets, tail_file_text, tail_lines  # noqa: E402


LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)


class RedactSecretsUnitTests(unittest.TestCase):
    def test_no_secrets_is_byte_identical(self) -> None:
        text = "build succeeded: 12 tests passed, 0 failed, took 3.2s"
        self.assertEqual(text, redact_secrets(text))

    def test_empty_and_none_do_not_raise(self) -> None:
        self.assertEqual("", redact_secrets(""))
        self.assertEqual("", redact_secrets(None))  # type: ignore[arg-type]

    def test_very_long_line_does_not_raise(self) -> None:
        text = "x" * 500_000 + " token=FAKElongvalue1234567890"
        result = redact_secrets(text)
        self.assertNotIn("FAKElongvalue1234567890", result)

    def test_replacement_character_does_not_raise(self) -> None:
        text = "corrupted bytes: ��� token=FAKEbadutf8value"
        result = redact_secrets(text)
        self.assertIn("�", result)
        self.assertNotIn("FAKEbadutf8value", result)

    def test_bearer_token_plain(self) -> None:
        text = "Authorization: Bearer re_FAKElivekey123"
        result = redact_secrets(text)
        self.assertNotIn("re_FAKElivekey123", result)
        self.assertIn("Bearer [REDACTED]", result)

    def test_bearer_token_ruby_hash_quoting(self) -> None:
        text = "'Authorization'=>'Bearer re_FAKElivekey123'"
        result = redact_secrets(text)
        self.assertNotIn("re_FAKElivekey123", result)
        self.assertIn("Bearer [REDACTED]", result)
        self.assertIn("'Authorization'=>'", result)

    def test_bearer_token_json_quoting(self) -> None:
        text = '"authorization": "Bearer re_FAKElivekey123"'
        result = redact_secrets(text)
        self.assertNotIn("re_FAKElivekey123", result)
        self.assertIn("Bearer [REDACTED]", result)
        self.assertIn('"authorization": "', result)

    def test_known_prefixes_bare(self) -> None:
        cases = [
            "sk-ant-FAKE0000fakekey",
            "sk-FAKE0000fakekey123",
            "re_FAKEfake000000",
            "ghp_FAKEFAKEFAKEFAKE00",
            "gho_FAKEFAKEFAKEFAKE00",
            "ghu_FAKEFAKEFAKEFAKE00",
            "ghs_FAKEFAKEFAKEFAKE00",
            "github_pat_FAKE00000000000000000000000000000000000000",
            "xoxb-FAKE-000000000000-FAKEFAKEFAKEFAKE",
            "xoxp-FAKE-000000000000-FAKEFAKEFAKEFAKE",
            "xoxa-FAKE-000000000000-FAKEFAKEFAKEFAKE",
            "xoxs-FAKE-000000000000-FAKEFAKEFAKEFAKE",
            "glpat-FAKE00000000000000000",
            "sk_live_FAKE0000fakekey",
            "sk_test_FAKE0000fakekey",
            "rk_live_FAKE0000fakekey",
            "rk_test_FAKE0000fakekey",
        ]
        for secret in cases:
            with self.subTest(secret=secret):
                text = f"credential leaked: {secret} in the output"
                result = redact_secrets(text)
                self.assertNotIn(secret, result, text)
                self.assertIn("[REDACTED]", result)
                self.assertIn("credential leaked:", result)
                self.assertIn("in the output", result)

    def test_akia_aws_key(self) -> None:
        text = "AWS key AKIAFAKE0000FAKE0000 was used"
        result = redact_secrets(text)
        self.assertNotIn("AKIAFAKE0000FAKE0000", result)
        self.assertIn("[REDACTED]", result)
        self.assertIn("AWS key", result)
        self.assertIn("was used", result)

    def test_prefix_does_not_fire_inside_ordinary_identifier(self) -> None:
        text = "failure_reason: the store_key lookup timed out after retry_count=3"
        self.assertEqual(text, redact_secrets(text))

    def test_generic_assignment_variants(self) -> None:
        variants = [
            "api_key=FAKEvalue123",
            "api-key=FAKEvalue123",
            "apikey=FAKEvalue123",
            "API_KEY=FAKEvalue123",
            "token: FAKEvalue123",
            "secret=FAKEvalue123",
            "password=FAKEvalue123",
            "passwd=FAKEvalue123",
            "access_key=FAKEvalue123",
            "private_key=FAKEvalue123",
            "client_secret=FAKEvalue123",
            "'api_key'=>'FAKEvalue123'",
            '"token": "FAKEvalue123"',
        ]
        for text in variants:
            with self.subTest(text=text):
                result = redact_secrets(text)
                self.assertNotIn("FAKEvalue123", result, text)
                self.assertIn("[REDACTED]", result, text)

    def test_generic_assignment_keeps_key_name_and_structure(self) -> None:
        text = 'config: {"api_key": "FAKEvalue123", "retries": 3}'
        result = redact_secrets(text)
        self.assertEqual(
            'config: {"api_key": "[REDACTED]", "retries": 3}',
            result,
        )

    def test_redacts_value_only_not_whole_line(self) -> None:
        text = (
            "Failure: expected 200 got 401, headers: "
            "{'Authorization'=>'Bearer re_FAKElivekey123', 'Content-Type'=>'application/json'}"
        )
        result = redact_secrets(text)
        self.assertIn("Failure: expected 200 got 401, headers:", result)
        self.assertIn("'Content-Type'=>'application/json'", result)
        self.assertNotIn("re_FAKElivekey123", result)

    def test_idempotent(self) -> None:
        text = (
            "'Authorization'=>'Bearer re_FAKElivekey123', api_key=FAKEvalue123, "
            "sk-ant-FAKE0000fakekey seen near AKIAFAKE0000FAKE0000"
        )
        once = redact_secrets(text)
        twice = redact_secrets(once)
        self.assertEqual(once, twice)
        self.assertNotIn("[[REDACTED]]", once)

    def test_fails_closed_when_redaction_itself_raises(self) -> None:
        # The one code path nobody watches: if redaction blows up it must NOT
        # hand back the cleartext it was asked to screen.
        import ringer

        boom = mock.Mock(side_effect=RuntimeError("regex exploded"))
        with mock.patch.object(ringer, "_BEARER_RE") as fake_re:
            fake_re.sub = boom
            result = redact_secrets("Authorization: Bearer re_FAKElivekey123")
        self.assertNotIn("re_FAKElivekey123", result)
        self.assertIn("redaction failed", result)
        self.assertIn("RuntimeError", result)


class TailFunnelRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log_path = Path(self.temp.name) / "worker.log"
        self.log_path.write_text(
            "starting worker\n"
            "Authorization: Bearer re_liveLookingKey123\n"
            "worker finished\n",
            encoding="utf-8",
        )

    def test_tail_lines_redacts(self) -> None:
        lines = tail_lines(self.log_path, 10)
        joined = "\n".join(lines)
        self.assertNotIn("re_liveLookingKey123", joined)
        self.assertIn("[REDACTED]", joined)
        self.assertIn("Bearer [REDACTED]", joined)
        self.assertIn("worker finished", joined)

    def test_tail_file_text_redacts(self) -> None:
        text = tail_file_text(self.log_path, 10_000)
        self.assertNotIn("re_liveLookingKey123", text)
        self.assertIn("[REDACTED]", text)
        self.assertIn("Bearer [REDACTED]", text)
        self.assertIn("worker finished", text)


class VerifierRedactionTests(unittest.TestCase):
    def verify(self, task: TaskSpec, taskdir: Path):
        return asyncio.run(Verifier().verify(task, taskdir))

    def test_check_output_excerpt_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            taskdir = Path(root) / "task"
            taskdir.mkdir()
            task = TaskSpec(
                key="leaky-check",
                spec=LONG_SPEC,
                check="echo \"Authorization: Bearer sk-ant-FAKE0000fakekey123\"",
            )
            result = self.verify(task, taskdir)

        self.assertNotIn("sk-ant-FAKE0000fakekey123", result.raw_output_excerpt)
        self.assertIn("[REDACTED]", result.raw_output_excerpt)
        self.assertIn("Authorization: Bearer", result.raw_output_excerpt)


class ScrubRunLogsScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_dir = Path(self.temp.name) / "state"
        runs_dir = self.state_dir / "runs"
        logs_dir = self.state_dir / "work" / "run-1" / "task-a" / "logs"
        runs_dir.mkdir(parents=True)
        logs_dir.mkdir(parents=True)
        self.run_json = runs_dir / "run-1.json"
        self.run_json.write_text(
            '{"check_output_tail": "Authorization: Bearer re_FAKEfake000000"}',
            encoding="utf-8",
        )
        self.worker_log = logs_dir / "worker.log"
        self.worker_log.write_text(
            "Authorization: Bearer re_FAKEfake000000\n",
            encoding="utf-8",
        )

    def run_script(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--state-dir", str(self.state_dir), *extra],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_dry_run_leaves_files_unchanged(self) -> None:
        before = self.run_json.read_text(encoding="utf-8")
        before_log = self.worker_log.read_text(encoding="utf-8")

        proc = self.run_script()

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("re_FAKEfake000000", before)
        self.assertEqual(before, self.run_json.read_text(encoding="utf-8"))
        self.assertEqual(before_log, self.worker_log.read_text(encoding="utf-8"))
        self.assertIn("would", proc.stdout)

    def test_write_scrubs_secrets(self) -> None:
        proc = self.run_script("--write")

        self.assertEqual(0, proc.returncode, proc.stderr)
        after_json = self.run_json.read_text(encoding="utf-8")
        after_log = self.worker_log.read_text(encoding="utf-8")
        self.assertNotIn("re_FAKEfake000000", after_json)
        self.assertIn("[REDACTED]", after_json)
        self.assertNotIn("re_FAKEfake000000", after_log)
        self.assertIn("[REDACTED]", after_log)

    def test_scrubbed_run_json_still_parses(self) -> None:
        # Regression: a run record embeds the worker spec as a JSON string whose
        # inner quotes are backslash-escaped. Redacting the file as raw TEXT
        # rewrites across that escaping and leaves a file that no longer parses
        # — it corrupted two real run records in a dry-run rehearsal before the
        # scrub was changed to walk the parsed structure instead.
        payload = {
            "run_name": "escaping-regression",
            "tasks": [
                {
                    "spec": 'send a request with {"token": "FAKEspecvalue123"} '
                            "and report what comes back",
                    "check_output_tail": "Authorization: Bearer re_FAKEfake000000",
                }
            ],
        }
        self.run_json.write_text(json.dumps(payload), encoding="utf-8")

        proc = self.run_script("--write")
        self.assertEqual(0, proc.returncode, proc.stderr)

        after = self.run_json.read_text(encoding="utf-8")
        reparsed = json.loads(after)  # must not raise
        task = reparsed["tasks"][0]
        self.assertNotIn("re_FAKEfake000000", after)
        self.assertNotIn("FAKEspecvalue123", after)
        self.assertIn("[REDACTED]", task["check_output_tail"])
        self.assertIn("and report what comes back", task["spec"])

    def test_scrubs_jsonl_model_log_line_by_line(self) -> None:
        # runs.jsonl is JSON Lines, not one document — the first sweep missed
        # it entirely and left credentials in the model log.
        jsonl = self.state_dir / "runs.jsonl"
        jsonl.write_text(
            json.dumps({"model": "sonnet", "note": "Bearer re_FAKEfake000000"}) + "\n"
            + json.dumps({"model": "haiku", "note": "clean row"}) + "\n",
            encoding="utf-8",
        )

        proc = self.run_script("--write")
        self.assertEqual(0, proc.returncode, proc.stderr)

        lines = [l for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(2, len(lines), "no record may be dropped")
        rows = [json.loads(l) for l in lines]  # every line must still parse
        self.assertNotIn("re_FAKEfake000000", jsonl.read_text(encoding="utf-8"))
        self.assertIn("[REDACTED]", rows[0]["note"])
        self.assertEqual("clean row", rows[1]["note"])

    def test_scrubs_artifact_html(self) -> None:
        # Artifact pages embed check output and are what a browser renders.
        artifacts = self.state_dir / "artifacts"
        artifacts.mkdir()
        page = artifacts / "run-1.html"
        page.write_text(
            "<pre>Authorization: Bearer re_FAKEfake000000</pre>",
            encoding="utf-8",
        )

        proc = self.run_script("--write")
        self.assertEqual(0, proc.returncode, proc.stderr)

        after = page.read_text(encoding="utf-8")
        self.assertNotIn("re_FAKEfake000000", after)
        self.assertIn("[REDACTED]", after)
        self.assertIn("<pre>", after)

    def test_rewriting_the_model_log_resets_the_read_cursor(self) -> None:
        # The scoreboard reads runs.jsonl from a cached byte offset. Rewriting
        # the log moves every offset after the first redaction, so a stale
        # cursor resumes mid-record and silently drops rows.
        jsonl = self.state_dir / "runs.jsonl"
        jsonl.write_text(
            json.dumps({"note": "Bearer re_FAKEfake000000"}) + "\n",
            encoding="utf-8",
        )
        db_path = self.state_dir / "ringer.db"
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute("CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO sync_state VALUES ('log_offset', '4096')")
            conn.commit()

        proc = self.run_script("--write")
        self.assertEqual(0, proc.returncode, proc.stderr)

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            offset = conn.execute(
                "SELECT value FROM sync_state WHERE key = 'log_offset'"
            ).fetchone()[0]
        self.assertEqual("0", offset)

    def test_read_cursor_is_left_alone_when_the_model_log_was_clean(self) -> None:
        jsonl = self.state_dir / "runs.jsonl"
        jsonl.write_text(json.dumps({"note": "nothing secret here"}) + "\n", encoding="utf-8")
        db_path = self.state_dir / "ringer.db"
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute("CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO sync_state VALUES ('log_offset', '4096')")
            conn.commit()

        proc = self.run_script("--write")
        self.assertEqual(0, proc.returncode, proc.stderr)

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            offset = conn.execute(
                "SELECT value FROM sync_state WHERE key = 'log_offset'"
            ).fetchone()[0]
        self.assertEqual("4096", offset, "an untouched log must not force a rebuild")


if __name__ == "__main__":
    unittest.main()
