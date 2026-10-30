#!/usr/bin/env python3
"""Tests for JSON token-stats capture for engine lanes."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402

FIXTURE_JSON = """{"session_id": "abc", "response": "OK.", "stats": {"models": {"gemini-3.1-flash-lite": {"api": {"totalRequests": 1}, "tokens": {"input": 2856, "prompt": 2856, "candidates": 38, "total": 3148, "cached": 0, "thoughts": 254, "tool": 0}, "roles": {"utility_router": {"tokens": {"input": 2856, "prompt": 2856, "candidates": 38, "total": 3148, "cached": 0, "thoughts": 254, "tool": 0}}}}, "gemini-3.1-pro-preview": {"api": {"totalRequests": 1}, "tokens": {"input": 10081, "prompt": 10081, "candidates": 2, "total": 10722, "cached": 0, "thoughts": 639, "tool": 0}, "roles": {"main": {"tokens": {"input": 10081, "prompt": 10081, "candidates": 2, "total": 10722, "cached": 0, "thoughts": 639, "tool": 0}}}}}, "tools": {"totalCalls": 0}, "files": {"totalLinesAdded": 0}}}"""


class TestParseTokenStatsJson(unittest.TestCase):
    def test_tjp_1_extracts_and_sums_paths(self):
        """[TJP-1] parse_token_stats_json extracts last parseable JSON and resolves paths, summing integers."""
        func = getattr(ringer, 'parse_token_stats_json', None)
        if func is None:
            self.fail("ringer.parse_token_stats_json not implemented")
            
        text = 'some text\n{"a": {"b": 10, "c": 20}, "d": 5}\nmore text'
        self.assertEqual(func(text, ["a.b"]), 10)
        self.assertEqual(func(text, ["a.*"]), 30)
        self.assertEqual(func(text, ["d"]), 5)
        self.assertEqual(func(text, ["a.b", "d"]), 15)

    def test_tjp_2_fixture_exact_count(self):
        """[TJP-2] paths=["stats.models.*.tokens.total"] against fixture yields exactly 13870 (no double count)."""
        func = getattr(ringer, 'parse_token_stats_json', None)
        if func is None:
            self.fail("ringer.parse_token_stats_json not implemented")
            
        result = func(FIXTURE_JSON, ["stats.models.*.tokens.total"])
        self.assertEqual(result, 13870)

    def test_tjp_3_embedded_noise(self):
        """[TJP-3] Extraction succeeds when JSON is preceded and followed by non-JSON noise lines."""
        func = getattr(ringer, 'parse_token_stats_json', None)
        if func is None:
            self.fail("ringer.parse_token_stats_json not implemented")
            
        text = (
            "Warning: 256-color support not detected.\n"
            '{"stats": {"models": {"old": {"tokens": {"total": 99999}}}}}\n'
            "Some mid-stream warning\n"
            f"{FIXTURE_JSON}\n"
            "[ringer.py] attempt 1 exited rc=0\n"
        )
        result = func(text, ["stats.models.*.tokens.total"])
        self.assertEqual(result, 13870)

    def test_tjp_4_no_json_or_no_match(self):
        """[TJP-4] Returns None when text has no parseable JSON or paths match nothing."""
        func = getattr(ringer, 'parse_token_stats_json', None)
        if func is None:
            self.fail("ringer.parse_token_stats_json not implemented")
            
        self.assertIsNone(func("just some text", ["a.b"]))
        self.assertIsNone(func('{"a": 1}', ["b.c"]))
        self.assertIsNone(func(FIXTURE_JSON, ["stats.models.*.tokens.nonexistent"]))

    def test_tjp_1b_float_leaves_ignored(self):
        """FINDING 2: with text '{"a": {"b": 10, "c": 2.5, "d": true}}' and paths ["a.*"], assert the result is exactly 10 — float and bool leaves are NOT summed (booleans are ints in Python; the implementation must exclude them explicitly, so assert 10, not 11)."""
        func = getattr(ringer, 'parse_token_stats_json', None)
        if func is None:
            self.fail("ringer.parse_token_stats_json not implemented")
        self.assertEqual(func('{"a": {"b": 10, "c": 2.5, "d": true}}', ["a.*"]), 10)

    def test_tjp_1c_empty_paths_returns_none(self):
        """FINDING 6: parse_token_stats_json(FIXTURE_JSON, []) returns None (function-level contract; distinct from the engine-level empty-tuple behavior already tested)."""
        func = getattr(ringer, 'parse_token_stats_json', None)
        if func is None:
            self.fail("ringer.parse_token_stats_json not implemented")
        self.assertIsNone(func(FIXTURE_JSON, []))

    def test_tjp_3b_pretty_printed_multiline_json(self):
        """REVIEW FINDING 1 (load-bearing): the real gemini CLI emits PRETTY-PRINTED multi-line JSON (json.dumps indent=2 style), not compact single-line. Re-serialize the existing two-model fixture with json.dumps(obj, indent=2), embed it with noise lines before AND after (noise after the closing brace), call parse_token_stats_json with ["stats.models.*.tokens.total"], assert exactly 13870. A line-oriented extractor must FAIL this test."""
        func = getattr(ringer, 'parse_token_stats_json', None)
        if func is None:
            self.fail("ringer.parse_token_stats_json not implemented")
        obj = json.loads(FIXTURE_JSON)
        pretty_json = json.dumps(obj, indent=2)
        text = f"Noise before\n{pretty_json}\nNoise after\n"
        self.assertEqual(func(text, ["stats.models.*.tokens.total"]), 13870)

    def test_tjp_4b_non_dict_mid_traversal(self):
        """FINDING 3: with text '{"a": 1}' and paths ["a.b.c"], assert the function returns None and does NOT raise (traversing through a scalar is a no-match, not a crash)."""
        func = getattr(ringer, 'parse_token_stats_json', None)
        if func is None:
            self.fail("ringer.parse_token_stats_json not implemented")
        self.assertIsNone(func('{"a": 1}', ["a.b.c"]))


class TestEngineConfigTokenJsonPaths(unittest.TestCase):
    def test_tjp_5_engine_config_and_load(self):
        """[TJP-5] EngineConfig has optional token_json_paths; load_engines parses TOML list."""
        config = ringer.EngineConfig(
            name="test",
            bin="/bin/test",
            args_template=("run",),
            full_access_args=(),
            sandbox_args=(),
            token_json_paths=("stats.models.*.tokens.total",)
        )
        self.assertEqual(config.token_json_paths, ("stats.models.*.tokens.total",))

        default_config = ringer.EngineConfig(
            name="test2",
            bin="/bin/test",
            args_template=("run",),
            full_access_args=(),
            sandbox_args=(),
        )
        self.assertEqual(getattr(default_config, 'token_json_paths', None), ())

        raw_toml = {
            "engine_with_paths": {
                "bin": "/bin/my_engine",
                "args_template": ["{spec}"],
                "token_json_paths": ["stats.models.*.tokens.total", "other.path"]
            },
            "engine_without_paths": {
                "bin": "/bin/no_paths",
                "args_template": ["{spec}"]
            }
        }
        engines = ringer.load_engines(raw_toml)
        
        self.assertIn("engine_with_paths", engines)
        self.assertEqual(
            getattr(engines["engine_with_paths"], 'token_json_paths', None),
            ("stats.models.*.tokens.total", "other.path")
        )
        
        self.assertIn("engine_without_paths", engines)
        self.assertEqual(
            getattr(engines["engine_without_paths"], 'token_json_paths', None),
            ()
        )


class TestResolveWorkerTokens(unittest.TestCase):
    def test_tjp_6_resolve_worker_tokens(self):
        """[TJP-6] resolve_worker_tokens uses parse_token_stats_json if configured, falling back to regex."""
        func = getattr(ringer, 'resolve_worker_tokens', None)
        if func is None:
            self.fail("ringer.resolve_worker_tokens not implemented")
            
        engine_with_paths = ringer.EngineConfig(
            name="with_paths",
            bin="/bin/test",
            args_template=("run",),
            full_access_args=(),
            sandbox_args=(),
            token_regex=r"tokens:\s*(\d+)",
            token_json_paths=("stats.models.*.tokens.total",)
        )
            
        engine_without_paths = ringer.EngineConfig(
            name="without_paths",
            bin="/bin/test",
            args_template=("run",),
            full_access_args=(),
            sandbox_args=(),
            token_regex=r"tokens:\s*(\d+)",
        )
        
        text_with_json = f"tokens: 100\n{FIXTURE_JSON}"
        text_no_json = "tokens: 100\nsome other text"
        
        self.assertEqual(func(text_with_json, engine_with_paths), 13870)
        self.assertEqual(func(text_no_json, engine_with_paths), 100)
        self.assertEqual(func(text_with_json, engine_without_paths), 100)
        self.assertIsNone(func("no match text", engine_without_paths))

    def test_tjp_6b_fallback_when_paths_match_nothing(self):
        """FINDING 4: resolve_worker_tokens with an engine whose token_json_paths match NOTHING in a text that DOES contain valid JSON plus a regex-matchable 'tokens used: 100' line must return 100 — the regex fallback applies when the JSON result is None for ANY reason (no JSON or no match), not only when JSON is absent."""
        func = getattr(ringer, 'resolve_worker_tokens', None)
        if func is None:
            self.fail("ringer.resolve_worker_tokens not implemented")
        
        engine_with_paths = ringer.EngineConfig(
            name="with_paths",
            bin="/bin/test",
            args_template=("run",),
            full_access_args=(),
            sandbox_args=(),
            token_regex=r"tokens used:\s*(\d+)",
            token_json_paths=("stats.models.*.tokens.total",)
        )
        
        text_with_json_but_no_match = 'tokens used: 100\n{"some": "json without stats"}'
        self.assertEqual(func(text_with_json_but_no_match, engine_with_paths), 100)


if __name__ == "__main__":
    unittest.main()
