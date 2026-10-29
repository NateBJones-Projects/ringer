#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    EngineConfig,
    load_engines,
    parse_claude_json_tokens,
    parse_worker_tokens,
)

EXPECTED = 16 + 32971 + 4745


def result_line(output_tokens: int = 4745, **extra: object) -> str:
    usage = {
        "input_tokens": 16,
        "cache_creation_input_tokens": 32971,
        "cache_read_input_tokens": 269119,
        "output_tokens": output_tokens,
        "server_tool_use": {"web_search_requests": 0},
        "cache_creation": {"ephemeral_5m_input_tokens": 32971, "output_tokens": 0},
        "iterations": [{"input_tokens": 3, "output_tokens": 606}],
    }
    obj = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "usage": usage,
        "modelUsage": {"sonnet": {"inputTokens": 1, "outputTokens": 606}},
        "total_cost_usd": 0.41,
    }
    obj.update(extra)
    return json.dumps(obj)


def engine(**kwargs: object) -> dict[str, EngineConfig]:
    section: dict[str, object] = {"args_template": ["{spec}"]}
    section.update(kwargs)
    return load_engines({"claude": section})


class ClaudeJsonTokenTests(unittest.TestCase):
    def test_top_level_usage_sums_without_cache_reads(self) -> None:
        self.assertEqual(parse_claude_json_tokens(result_line()), EXPECTED)

    def test_surrounded_by_log_lines(self) -> None:
        text = f"[ringer.py] start\n{result_line()}\n[ringer.py] done\n"
        self.assertEqual(parse_claude_json_tokens(text), EXPECTED)

    def test_no_result_object(self) -> None:
        self.assertIsNone(parse_claude_json_tokens("[ringer.py] nothing\n"))
        self.assertIsNone(parse_claude_json_tokens(json.dumps({"type": "assistant", "usage": {}})))
        self.assertIsNone(parse_claude_json_tokens(json.dumps({"type": "result"})))

    def test_truncated_json(self) -> None:
        self.assertIsNone(parse_claude_json_tokens(result_line()[:-40]))

    def test_last_result_wins_and_bad_fields_are_zero(self) -> None:
        first = result_line(output_tokens=1)
        last = json.dumps(
            {"type": "result", "usage": {"input_tokens": "x", "cache_creation_input_tokens": True, "output_tokens": 7}}
        )
        self.assertEqual(parse_claude_json_tokens(f"{first}\n{last}\n"), 7)

    def test_token_format_loading_and_rejection(self) -> None:
        self.assertEqual(engine(token_format="claude-json")["claude"].token_format, "claude-json")
        self.assertIsNone(engine()["claude"].token_format)
        with self.assertRaises(ValueError) as ctx:
            engine(token_format="nope")
        self.assertIn("token_format", str(ctx.exception))
        self.assertIn("claude", str(ctx.exception))

    def test_token_format_inherited_from_base(self) -> None:
        raw = {"codex": {"token_format": "claude-json"}}
        self.assertEqual(load_engines(raw)["codex"].token_format, "claude-json")

    def test_parse_worker_tokens_falls_back_to_regex(self) -> None:
        regex = r"tokens\s+used\s*:?\s*([0-9]+)"
        eng = engine(token_format="claude-json", token_regex=regex)["claude"]
        self.assertEqual(parse_worker_tokens("tokens used: 123", eng), 123)
        self.assertEqual(parse_worker_tokens(result_line(), eng), EXPECTED)
        plain = engine(token_regex=regex)["claude"]
        self.assertEqual(parse_worker_tokens(result_line() + "\ntokens used: 9", plain), 9)

    def test_misescaped_regex_warns(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            engine(token_regex=r"tokens\\s+used")
        out = err.getvalue()
        self.assertEqual(len(out.strip().splitlines()), 1)
        for word in ("token_regex", "claude", "backslash"):
            self.assertIn(word, out)

    def test_correct_regex_does_not_warn(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            engine(token_regex=r"tokens\s+used")
        self.assertEqual(err.getvalue(), "")


class ClaudeJsonEndToEndTests(unittest.TestCase):
    def test_run_records_claude_json_tokens(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ringer-claude-tokens-") as tmp:
            root = Path(tmp)
            jsonl = root / "runs.jsonl"
            script = root / "fake_claude.py"
            script.write_text(
                "import sys\n"
                "open('out.txt', 'w').write('done')\n"
                f"print({result_line()!r})\n",
                encoding="utf-8",
            )
            config = root / "config.toml"
            config.write_text(
                "\n".join(
                    [
                        f'state_dir = "{root / "state"}"',
                        "dashboard_port_base = 18797",
                        "allow_full_access = false",
                        "",
                        "[eval]",
                        'backend = "jsonl"',
                        f'jsonl_path = "{jsonl}"',
                        "",
                        "[engines.fakeclaude]",
                        f"bin = {json.dumps(sys.executable)}",
                        f"args_template = {json.dumps([str(script), '{spec}'])}",
                        "sandbox_args = []",
                        "full_access_args = []",
                        'token_format = "claude-json"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            manifest = root / "m.json"
            manifest.write_text(
                json.dumps(
                    {
                        "run_name": "claude-tokens",
                        "workdir": str(root / "work"),
                        "max_parallel": 1,
                        "tasks": [
                            {
                                "key": "t1",
                                "engine": "fakeclaude",
                                "spec": "Write out.txt.",
                                "expect_files": ["out.txt"],
                                "check": 'test "$(cat out.txt)" = done',
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["RINGER_NO_SELF_UPDATE"] = "1"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "ringer.py"),
                    "--config",
                    str(config),
                    "run",
                    str(manifest),
                    "--identity",
                    "test-runner",
                    "--no-dashboard",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=60,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual([row["worker_tokens"] for row in rows], [EXPECTED])


if __name__ == "__main__":
    unittest.main()
