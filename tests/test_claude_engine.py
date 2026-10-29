import json
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
    aggregate_model_log_rows,
    parse_claude_code_json_result,
    VerifyResult,
    WorkerResult,
)


class ClaudeCodeResultTests(unittest.TestCase):
    def envelope(self, requested: str) -> str:
        return json.dumps(
            {
                "type": "result",
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 30,
                    "output_tokens": 40,
                },
                "modelUsage": {
                    "claude-haiku-4-5-20251001": {"canonicalModel": "claude-haiku-4-5"},
                    requested: {"canonicalModel": requested},
                },
                "is_error": False,
            }
        )

    def test_tokens_exclude_internal_title_model_and_match_requested_model(self) -> None:
        result = parse_claude_code_json_result(self.envelope("claude-sonnet-5"), "sonnet")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(100, result.tokens)
        self.assertEqual("claude-sonnet-5", result.reported_model)

    def test_all_alias_families_match_the_requested_entry(self) -> None:
        for alias, key in (
            ("sonnet", "claude-sonnet-5"),
            ("opus", "claude-opus-5"),
            ("fable", "claude-fable-5-1"),
            ("haiku", "claude-haiku-4-5"),
        ):
            result = parse_claude_code_json_result(self.envelope(key), alias)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(key, result.reported_model)

    def test_api_error_is_infrastructure_and_non_scoreable(self) -> None:
        result = parse_claude_code_json_result(
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "terminal_reason": "api_error",
                    "api_error_status": 429,
                    "message": "rate limit exceeded",
                }
            ),
            "sonnet",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.scoreable)
        self.assertEqual("infrastructure", result.failure_kind)
        self.assertIn("429", result.error or "")
        self.assertIn("rate limit", result.error or "")

    def test_agent_error_without_api_markers_stays_scoreable(self) -> None:
        result = parse_claude_code_json_result(
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "subtype": "error_max_turns",
                    "message": "agent gave up after max turns",
                }
            ),
            "sonnet",
        )
        self.assertTrue(result.scoreable)
        self.assertEqual("agent", result.failure_kind)
        self.assertIn("error_max_turns", result.error or "")

    def test_embedded_json_in_result_string_is_not_mistaken_for_the_envelope(self) -> None:
        # A single pretty-printed envelope whose own `result` text happens to
        # quote something that looks like a JSON result object. The bracket
        # scan this replaces would have grabbed the embedded fragment.
        text = json.dumps(
            {
                "type": "result",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "result": 'example: {"type": "result", "usage": {"output_tokens": 999}}',
                "is_error": False,
            },
            indent=2,
        )
        result = parse_claude_code_json_result(text)
        self.assertEqual(2, result.tokens)

    def test_ambiguous_same_family_model_usage_returns_none(self) -> None:
        result = parse_claude_code_json_result(
            json.dumps(
                {
                    "type": "result",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "modelUsage": {
                        "claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"},
                        "claude-sonnet-4-6": {"canonicalModel": "claude-sonnet-4-6"},
                    },
                }
            ),
            "sonnet",
        )
        self.assertIsNone(result.reported_model)

    def test_last_result_envelope_wins_over_diagnostic_object(self) -> None:
        text = "\n".join([
            json.dumps({"type": "result", "usage": {"input_tokens": 2, "output_tokens": 3}}),
            json.dumps({"type": "diagnostic", "message": "not a result"}),
            json.dumps({"type": "result", "usage": {"input_tokens": 7, "output_tokens": 11}}),
        ])
        result = parse_claude_code_json_result(text)
        self.assertEqual(18, result.tokens)

    def test_missing_result_envelope_is_non_scoreable_harness_failure(self) -> None:
        result = parse_claude_code_json_result(json.dumps({"type": "diagnostic"}))
        self.assertFalse(result.scoreable)
        self.assertEqual("harness", result.failure_kind)

    def test_usage_sums_present_counters_and_rejects_bool(self) -> None:
        result = parse_claude_code_json_result(json.dumps({
            "type": "result",
            "usage": {"input_tokens": 4, "output_tokens": 6},
            "modelUsage": {"sonnet": {"inputTokens": 9000}},
        }))
        self.assertEqual(10, result.tokens)
        rejected = parse_claude_code_json_result(json.dumps({
            "type": "result", "usage": {"input_tokens": True, "output_tokens": 6}
        }))
        self.assertIsNone(rejected.tokens)


class RunnerNonScoreableTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_scoreable_infrastructure_skips_verifier_and_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = Manifest.from_obj({
                "run_name": "non-scoreable",
                "workdir": str(root / "work"),
                "tasks": [{
                    "key": "task",
                    "spec": "unused",
                    "check": "exit 0",
                    "max_attempts": 3,
                    "engine": "fake",
                }],
            })
            engine = EngineConfig(
                name="fake", bin="unused", args_template=("{spec}",),
                full_access_args=(), sandbox_args=(), model_default="fake-model",
            )
            config = AppConfig(
                path=None, identity_default=None, state_dir=root / "state",
                dashboard_port_base=8787, hud_port=8700, hud_app_path=None,
                allow_full_access=False,
                eval=EvalConfig(backend="jsonl", jsonl_path=root / "eval.jsonl"),
                engines={"fake": engine},
                artifact=ArtifactConfig(
                    enabled=False, out_template=str(root / "live.html"),
                    report_template=str(root / "report.html"), index_out=root / "index.html",
                ),
            )
            runner = RingerRunner(manifest, config=config, identity="tester", dashboard_enabled=False)
            calls = {"worker": 0, "verify": 0}

            async def fake_worker(runtime, spec, attempt):
                calls["worker"] += 1
                return WorkerResult(1, False, None, error="network unavailable", scoreable=False, failure_kind="infrastructure")

            async def verify(*args, **kwargs):
                calls["verify"] += 1
                return VerifyResult(True, 0, False, "should not run")

            runner._run_worker = fake_worker
            runner.verifier.verify = verify
            await runner._run_task(runner.runtimes[0])
            row = json.loads((root / "eval.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(1, calls["worker"])
            self.assertEqual(0, calls["verify"])
            self.assertEqual("ERROR", row["verdict"])
            self.assertFalse(row["scoreable"])
            self.assertEqual("infrastructure", row["failure_kind"])

    def test_aggregation_excludes_explicitly_non_scoreable_rows_but_keeps_old_rows(self) -> None:
        base = {
            "run_id": "run",
            "worker_engine": "claude",
            "model": "claude-sonnet-5",
            "task_type": "code-feature",
            "retry": False,
            "duration_ms": 1,
            "worker_tokens": 100,
        }
        rows = [
            {**base, "task_key": "infra-only", "verdict": "ERROR", "scoreable": False},
            {**base, "task_key": "historical", "verdict": "PASS"},
        ]
        groups = aggregate_model_log_rows(rows)
        self.assertEqual(1, len(groups))
        self.assertEqual(1, groups[0]["tasks"])
        self.assertEqual(1, groups[0]["attempts"])


if __name__ == "__main__":
    unittest.main()
