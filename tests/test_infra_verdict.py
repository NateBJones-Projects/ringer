#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402


def toml_string(value: object) -> str:
    return json.dumps(str(value))


class ClassifyInfraTests(unittest.TestCase):
    def test_each_category(self) -> None:
        cases = {
            "auth": "error: refresh_token_invalidated, please sign in",
            "billing": "You can only afford 10 tokens",
            "rate-limit": "Claude usage limit reached",
            "model-unavailable": "No endpoints found for foo/bar",
            "interop": "UtilBindVsock failed",
            "cli": "Input must be provided either through stdin or as a prompt argument",
        }
        for category, line in cases.items():
            with self.subTest(category=category):
                reason = ringer.classify_infra(1, False, 500.0, f"noise\n{line}\n")
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(f"{category}: "), reason)
                self.assertIn(line.split()[0], reason)

    def test_weak_signature_needs_fast_exit(self) -> None:
        out = "HTTP 429 from upstream\n"
        self.assertTrue(ringer.classify_infra(1, False, 5.0, out).startswith("rate-limit: "))
        self.assertIsNone(ringer.classify_infra(1, False, 61.0, out))
        self.assertIsNotNone(ringer.classify_infra(1, False, 600.0, "rate_limit_error: slow down\n"))

    def test_requires_nonzero_int_and_no_timeout(self) -> None:
        out = "refresh_token_invalidated\n"
        self.assertIsNone(ringer.classify_infra(0, False, 1.0, out))
        self.assertIsNone(ringer.classify_infra(1, True, 1.0, out))
        self.assertIsNone(ringer.classify_infra(None, False, 1.0, out))
        self.assertIsNotNone(ringer.classify_infra(-9, False, 1.0, out))

    def test_unrecognized_fast_failure_is_not_infra(self) -> None:
        self.assertIsNone(ringer.classify_infra(1, False, 0.5, "mock-worker: simulated failure\n"))

    def test_only_last_20_lines_are_searched(self) -> None:
        lines = ["not logged in"] + [f"line {i}" for i in range(20)]
        self.assertIsNone(ringer.classify_infra(1, False, 1.0, "\n".join(lines)))
        lines = ["not logged in"] + [f"line {i}" for i in range(19)]
        self.assertIsNotNone(ringer.classify_infra(1, False, 1.0, "\n".join(lines)))

    def test_ansi_codes_are_stripped(self) -> None:
        reason = ringer.classify_infra(1, False, 1.0, "\x1b[31mnot\x1b[0m logged in\n")
        self.assertEqual("auth: not logged in", reason)

    def test_category_order_and_long_line_excerpt(self) -> None:
        reason = ringer.classify_infra(1, False, 1.0, "insufficient credits\nnot logged in\n")
        self.assertTrue(reason.startswith("auth: "), reason)
        long_line = "x" * 500 + " model_not_found " + "y" * 500
        reason = ringer.classify_infra(1, False, 1.0, long_line)
        self.assertIn("model_not_found", reason)
        self.assertLessEqual(len(reason), len("model-unavailable: ") + 220)


class VerdictAndScoreboardTests(unittest.TestCase):
    def test_verdict_precedence(self) -> None:
        ok = ringer.VerifyResult(ok=True, check_returncode=0, check_timed_out=False, raw_output_excerpt="")
        bad = ringer.VerifyResult(ok=False, check_returncode=1, check_timed_out=False, raw_output_excerpt="")
        infra = ringer.WorkerResult(returncode=1, timed_out=False, tokens=None, infra_reason="auth: x")
        self.assertEqual("PASS", ringer.verdict_for(infra, ok))
        self.assertEqual("INFRA", ringer.verdict_for(infra, bad))
        errored = ringer.WorkerResult(
            returncode=None, timed_out=False, tokens=None, error="e", infra_reason="auth: x"
        )
        self.assertEqual("ERROR", ringer.verdict_for(errored, bad))
        timed = ringer.WorkerResult(returncode=1, timed_out=True, tokens=None, infra_reason="auth: x")
        self.assertEqual("TIMEOUT", ringer.verdict_for(timed, ok))
        plain = ringer.WorkerResult(returncode=1, timed_out=False, tokens=None)
        self.assertEqual("FAIL", ringer.verdict_for(plain, bad))

    def test_scoreboard_excludes_infra_rows(self) -> None:
        def row(key: str, verdict: str, at: str, model: str = "m1") -> dict:
            return {
                "run_id": "r",
                "task_key": key,
                "verdict": verdict,
                "logged_at": at,
                "model": model,
                "worker_engine": "e",
                "task_type": "t",
                "duration_ms": 10,
            }

        rows = [
            row("a", "INFRA", "2026-01-01T00:00:00"),
            row("b", "PASS", "2026-01-01T00:00:01"),
            row("c", "INFRA", "2026-01-01T00:00:02", model="m2"),
        ]
        log_rows = ringer.aggregate_model_log_rows(rows)
        self.assertEqual([("m1", 1, 1)], [(g["model"], g["tasks"], g["passed"]) for g in log_rows])
        board = ringer.aggregate_model_scoreboard_rows(rows)
        self.assertEqual(["m1"], [m["model"] for m in board])
        self.assertEqual(1, board[0]["tasks"])
        self.assertEqual(0, board[0]["failed"])


class InfraRenderingTests(unittest.TestCase):
    def test_headline_shape(self) -> None:
        text = ringer.infra_headline("auth: not logged in", "FAIL: out.txt missing")
        self.assertTrue(text.startswith("[ringer] INFRA"), text)
        self.assertIn("never got to work", text)
        self.assertIn("auth: not logged in", text)
        self.assertIn("\n\nFAIL: out.txt missing", text)

    def test_results_page_never_calls_infra_a_pass_or_a_failed_check(self) -> None:
        task = {"key": "t", "status": "fail", "verdict": "INFRA"}
        self.assertEqual("fail", ringer.task_state_bucket(task["status"]))
        self.assertEqual("failed to start", ringer.task_state_word(task["status"], task))
        line = ringer.plain_transition_line("t", "running", "fail", task)
        self.assertIn("never got to work", line)
        plain = {"key": "t", "status": "fail", "verdict": "FAIL"}
        self.assertEqual("failed", ringer.task_state_word(plain["status"], plain))
        state = {"tasks": [task], "elapsed_s": 1}
        self.assertIn("never got to work", ringer.final_briefing_sentence(state))
        self.assertNotIn("failed after retry", ringer.final_briefing_sentence(state))


class InfraEndToEndTests(unittest.TestCase):
    def test_dead_lane_trips_and_other_lane_unaffected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home, ringer_home = root / "home", root / "ringer-home"
            state_dir, workdir = root / "state", root / "work"
            home.mkdir()
            ringer_home.mkdir()
            fake = root / "fake_engine.py"
            fake.write_text(
                "import sys\n"
                "print('401 Unauthorized: refresh_token_invalidated')\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            mock_worker = ROOT / "engines" / "mock_worker.py"
            config_path = root / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        f"state_dir = {toml_string(state_dir)}",
                        "",
                        "[eval]",
                        'backend = "jsonl"',
                        f"jsonl_path = {toml_string(root / 'runs.jsonl')}",
                        "",
                        "[artifact]",
                        "enabled = false",
                        "",
                        "[engines.deadlane]",
                        f"bin = {toml_string(sys.executable)}",
                        f'args_template = [{toml_string(fake)}, "{{spec}}"]',
                        "sandbox_args = []",
                        "full_access_args = []",
                        "",
                        "[engines.mock]",
                        f"bin = {toml_string(sys.executable)}",
                        f'args_template = [{toml_string(mock_worker)}, "{{spec}}"]',
                        "sandbox_args = []",
                        "full_access_args = []",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            check = "test -f out.txt || { echo FAIL: out.txt missing; exit 1; }"
            tasks = [
                {
                    "key": f"dead-{i}",
                    "engine": "deadlane",
                    "spec": "do it",
                    "check": check,
                    "expect_files": ["out.txt"],
                }
                for i in (1, 2, 3)
            ]
            tasks.append(
                {
                    "key": "live",
                    "engine": "mock",
                    "spec": "MOCK_FILE: out.txt\nhi\nMOCK_END",
                    "check": "grep -q hi out.txt",
                    "expect_files": ["out.txt"],
                }
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "infra-test",
                        "workdir": str(workdir),
                        "max_parallel": 1,
                        "worktrees": False,
                        "tasks": tasks,
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["RINGER_HOME"] = str(ringer_home)
            env["RINGER_NO_SELF_UPDATE"] = "1"
            env["XDG_CONFIG_HOME"] = str(root / "xdg-config")
            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "run",
                    str(manifest_path),
                    "--config",
                    str(config_path),
                    "--no-dashboard",
                    "--identity",
                    "infra-test",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=60,
            )
            combined = proc.stdout + proc.stderr
            self.assertEqual(1, proc.returncode, combined)
            for key, attempts in (("dead-1", 1), ("dead-2", 1), ("dead-3", 0)):
                self.assertRegex(
                    combined,
                    re.compile(rf"^{key}\s+fail\s+INFRA\s+{attempts}\s+", re.MULTILINE),
                    combined,
                )
            self.assertRegex(combined, re.compile(r"^live\s+pass\s+PASS\s+1\s+", re.MULTILINE), combined)
            self.assertIn("never got to work", combined)

            rows = [
                json.loads(line)
                for line in (root / "runs.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            by_task: dict[str, list[dict]] = {}
            for entry in rows:
                by_task.setdefault(entry["task_key"], []).append(entry)
            self.assertEqual(1, len(by_task["dead-1"]))
            self.assertEqual("INFRA", by_task["dead-1"][0]["verdict"])
            self.assertIn("infra_reason=auth:", by_task["dead-1"][0]["notes"])
            self.assertIn("worker_output_last_1500_chars:", by_task["dead-1"][0]["notes"])
            self.assertNotIn("dead-3", by_task)
            self.assertEqual(["PASS"], [e["verdict"] for e in by_task["live"]])
            self.assertNotIn("worker_output_last_1500_chars:", by_task["live"][0]["notes"])

            texts = [p.read_text(encoding="utf-8") for p in state_dir.glob("**/*.json")]
            self.assertTrue(any("skipped" in t and "lane" in t for t in texts))


if __name__ == "__main__":
    unittest.main(verbosity=2)
