#!/usr/bin/env python3
from __future__ import annotations

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
    parse_opencode_json_tokens,
    parse_token_count,
    parse_worker_tokens,
)

# Two steps: input + cache write + output + reasoning, cache reads excluded.
EXPECTED = (44660 + 120 + 20 + 35) + (66 + 0 + 14 + 0)
OLD_SAMPLE_REGEX = r'"tokens":\{"total":([0-9]+)'


def event(kind: str, **part: object) -> str:
    return json.dumps(
        {"type": kind, "timestamp": 1, "sessionID": "ses_x", "part": {"type": kind.replace("_", "-"), **part}},
        separators=(",", ":"),
    )


def step(tokens: object, reason: str = "stop") -> str:
    return event("step_finish", reason=reason, cost=0, tokens=tokens)


def opencode_output() -> str:
    return "\n".join(
        [
            event("step_start"),
            event("tool_use", tool="write", state={"output": '"tokens":{"input":999}'}),
            step({"input": 44660, "output": 20, "reasoning": 35, "cache": {"read": 0, "write": 120}}, "tool-calls"),
            event("step_start"),
            event("text", text="done"),
            step({"input": 66, "output": 14, "reasoning": 0, "cache": {"read": 44659, "write": 0}}),
        ]
    )


def engine(**kwargs: object) -> dict[str, EngineConfig]:
    section: dict[str, object] = {"args_template": ["{spec}"]}
    section.update(kwargs)
    return load_engines({"opencode": section})


class OpenCodeJsonTokenTests(unittest.TestCase):
    def test_sums_steps_without_cache_reads(self) -> None:
        self.assertEqual(parse_opencode_json_tokens(opencode_output()), EXPECTED)

    def test_old_sample_regex_never_matches(self) -> None:
        self.assertIsNone(parse_token_count(opencode_output(), OLD_SAMPLE_REGEX))

    def test_surrounded_by_log_lines(self) -> None:
        text = f"[ringer.py] attempt 1 started\n{opencode_output()}\n[ringer.py] attempt 1 exited rc=0\n"
        self.assertEqual(parse_opencode_json_tokens(text), EXPECTED)

    def test_no_step_finish_is_unrecorded(self) -> None:
        self.assertIsNone(parse_opencode_json_tokens("[ringer.py] nothing\n"))
        self.assertIsNone(parse_opencode_json_tokens(event("step_start") + "\n" + event("text", text="hi")))
        self.assertIsNone(parse_opencode_json_tokens(event("step_finish", reason="stop")))

    def test_truncated_line_is_skipped(self) -> None:
        full = step({"input": 5, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}})
        text = full[:-20] + "\n" + full
        self.assertEqual(parse_opencode_json_tokens(text), 6)

    def test_bad_fields_count_as_zero(self) -> None:
        text = step({"input": "x", "output": True, "reasoning": 3, "cache": "nope"})
        self.assertEqual(parse_opencode_json_tokens(text), 3)

    def test_token_format_loading_and_rejection(self) -> None:
        self.assertEqual(engine(token_format="opencode-json")["opencode"].token_format, "opencode-json")
        self.assertIsNone(engine()["opencode"].token_format)
        with self.assertRaises(ValueError) as ctx:
            engine(token_format="nope")
        self.assertIn("token_format", str(ctx.exception))
        self.assertIn("opencode", str(ctx.exception))

    def test_token_format_inherited_from_base(self) -> None:
        raw = {"codex": {"token_format": "opencode-json"}}
        self.assertEqual(load_engines(raw)["codex"].token_format, "opencode-json")

    def test_parse_worker_tokens_falls_back_to_regex(self) -> None:
        regex = r"tokens\s+used\s*:?\s*([0-9]+)"
        eng = engine(token_format="opencode-json", token_regex=regex)["opencode"]
        self.assertEqual(parse_worker_tokens("tokens used: 123", eng), 123)
        self.assertEqual(parse_worker_tokens(opencode_output(), eng), EXPECTED)
        plain = engine(token_regex=regex)["opencode"]
        self.assertEqual(parse_worker_tokens(opencode_output() + "\ntokens used: 9", plain), 9)


class OpenCodeJsonEndToEndTests(unittest.TestCase):
    def test_run_records_opencode_json_tokens(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ringer-opencode-tokens-") as tmp:
            root = Path(tmp)
            jsonl = root / "runs.jsonl"
            script = root / "fake_opencode.py"
            script.write_text(
                "open('out.txt', 'w').write('done')\n" f"print({opencode_output()!r})\n",
                encoding="utf-8",
            )
            config = root / "config.toml"
            config.write_text(
                "\n".join(
                    [
                        f'state_dir = "{root / "state"}"',
                        "dashboard_port_base = 18798",
                        "allow_full_access = false",
                        "",
                        "[eval]",
                        'backend = "jsonl"',
                        f'jsonl_path = "{jsonl}"',
                        "",
                        "[engines.fakeopencode]",
                        f"bin = {json.dumps(sys.executable)}",
                        f"args_template = {json.dumps([str(script), '{spec}'])}",
                        "sandbox_args = []",
                        "full_access_args = []",
                        'token_format = "opencode-json"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            manifest = root / "m.json"
            manifest.write_text(
                json.dumps(
                    {
                        "run_name": "opencode-tokens",
                        "workdir": str(root / "work"),
                        "max_parallel": 1,
                        "tasks": [
                            {
                                "key": "t1",
                                "engine": "fakeopencode",
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
            env["HOME"] = str(root / "home")
            env["RINGER_HOME"] = str(root / "ringer-home")
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
