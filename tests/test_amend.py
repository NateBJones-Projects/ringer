#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ringer_under_test", ROOT / "ringer.py")
ringer = importlib.util.module_from_spec(spec)
sys.modules["ringer_under_test"] = ringer
spec.loader.exec_module(ringer)


def row(task_key: str, verdict: str, at: str, *, retry: bool = False, run_id: str = "r1") -> dict:
    return {
        "run_id": run_id,
        "task_key": task_key,
        "logged_at": at,
        "verdict": verdict,
        "retry": retry,
        "model": "m/x",
        "worker_engine": "mock",
        "task_type": "code",
    }


def amendment(task_key: str, cause: str, attempt: int | None, run_id: str = "r1") -> dict:
    return {"run_id": run_id, "task_key": task_key, "attempt": attempt, "cause": cause, "note": "x" * 12}


class AmendLogicTests(unittest.TestCase):
    def test_attempt_numbering_orders_by_time_then_retry(self) -> None:
        rows = [
            row("t", "PASS", "2026-01-01T00:00:02", retry=True),
            row("t", "FAIL", "2026-01-01T00:00:01"),
            row("t", "FAIL", "2026-01-01T00:00:02"),
        ]
        numbers = ringer.model_log_attempt_numbers(rows)
        self.assertEqual([numbers[id(r)][1] for r in rows], [3, 1, 2])

    def test_latest_wins_and_worker_restores(self) -> None:
        rows = [row("t", "FAIL", "2026-01-01T00:00:01")]
        out = ringer.apply_amendments(rows, [amendment("t", "check", 1), amendment("t", "worker", None)])
        self.assertEqual(out[0]["amended_cause"], "worker")
        self.assertEqual(len(ringer.drop_amended_rows(out)), 1)
        out = ringer.apply_amendments(rows, [amendment("t", "worker", None), amendment("t", "spec", 1)])
        self.assertEqual(out[0]["amended_cause"], "spec")
        self.assertEqual(ringer.drop_amended_rows(out), [])
        self.assertNotIn("amended_cause", rows[0])

    def test_attempt_null_skips_pass_rows(self) -> None:
        rows = [
            row("t", "FAIL", "2026-01-01T00:00:01"),
            row("t", "PASS", "2026-01-01T00:00:02", retry=True),
        ]
        out = ringer.apply_amendments(rows, [amendment("t", "check", None)])
        self.assertEqual(out[0]["amended_cause"], "check")
        self.assertNotIn("amended_cause", out[1])
        out = ringer.apply_amendments(rows, [amendment("t", "check", 2)])
        self.assertNotIn("amended_cause", out[1])

    def test_both_aggregators_exclude_amended_rows(self) -> None:
        rows = [
            row("a", "FAIL", "2026-01-01T00:00:01"),
            row("a", "PASS", "2026-01-01T00:00:02", retry=True),
            row("b", "FAIL", "2026-01-01T00:00:03"),
        ]
        amended = ringer.apply_amendments(rows, [amendment("b", "spec", None)])
        for result in (ringer.aggregate_model_log_rows(amended), ringer.aggregate_model_scoreboard_rows(amended)):
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["tasks"], 1)
            self.assertEqual(result[0]["attempts"], 2)
            self.assertEqual(result[0]["failed"], 0)
        # Unamended, task b counts against the model.
        self.assertEqual(ringer.aggregate_model_log_rows(rows)[0]["tasks"], 2)
        self.assertEqual(ringer.aggregate_model_scoreboard_rows(rows)[0]["failed"], 1)

    def test_first_attempt_dropped_judges_remaining(self) -> None:
        rows = [
            row("a", "FAIL", "2026-01-01T00:00:01"),
            row("a", "PASS", "2026-01-01T00:00:02", retry=True),
        ]
        before = ringer.aggregate_model_log_rows(rows)[0]
        self.assertEqual(before["first_try_pass_rate"], 0.0)
        amended = ringer.apply_amendments(rows, [amendment("a", "check", 1)])
        group = ringer.aggregate_model_log_rows(amended)[0]
        self.assertEqual(group["attempts"], 1)
        self.assertEqual(group["failed"], 0)
        self.assertEqual(group["first_try_pass_rate"], 1.0)

    def test_load_amendments_tolerates_missing_and_garbage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = ringer.amendments_path(Path(temp) / "runs.jsonl")
            self.assertEqual(path.name, "amendments.jsonl")
            self.assertEqual(ringer.load_amendments(path), [])
            path.write_text('not json\n{"cause": "check"}\n', encoding="utf-8")
            self.assertEqual(ringer.load_amendments(path), [{"cause": "check"}])


class AmendCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.root = root
        (root / "home").mkdir()
        self.log = root / "runs.jsonl"
        self.config = root / "config.toml"
        self.config.write_text(
            "\n".join(
                [
                    f"state_dir = {json.dumps(str(root / 'state'))}",
                    "",
                    "[eval]",
                    'backend = "jsonl"',
                    f"jsonl_path = {json.dumps(str(self.log))}",
                    "",
                    "[artifact]",
                    "enabled = false",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        rows = [
            row("t1", "FAIL", "2026-01-01T00:00:01"),
            row("t1", "FAIL", "2026-01-01T00:00:02", retry=True),
            row("ok", "PASS", "2026-01-01T00:00:03"),
            row("t2", "FAIL", "2026-01-01T00:00:04"),
            row("t2", "PASS", "2026-01-01T00:00:05", retry=True),
        ]
        self.log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def ringer(self, *args: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["HOME"] = str(self.root / "home")
        env["RINGER_HOME"] = str(self.root / "ringer-home")
        env["XDG_CONFIG_HOME"] = str(self.root / "xdg")
        env["RINGER_NO_CATALOG_REFRESH"] = "1"
        env["RINGER_NO_SELF_UPDATE"] = "1"
        env["USER"] = "tester"
        return subprocess.run(
            [sys.executable, str(ROOT / "ringer.py"), *args, "--config", str(self.config)],
            env=env,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def amendments(self) -> list[dict]:
        return ringer.load_amendments(ringer.amendments_path(self.log))

    def test_validation_errors(self) -> None:
        note = "check was broken"
        cases = [
            (["amend", "nope", "t1", "--cause", "check", "--note", note], "not found"),
            (["amend", "r1", "nope", "--cause", "check", "--note", note], "not found"),
            (["amend", "r1", "t1", "--cause", "check", "--note", note, "--attempt", "9"], "attempt"),
            (["amend", "r1", "ok", "--cause", "check", "--note", note, "--attempt", "1"], "PASS"),
            (["amend", "r1", "ok", "--cause", "check", "--note", note], "nothing to amend"),
            (["amend", "r1", "t1", "--cause", "check", "--note", "  short  "], "note"),
        ]
        for argv, text in cases:
            proc = self.ringer(*argv)
            self.assertEqual(proc.returncode, 2, argv)
            self.assertIn(text, proc.stdout + proc.stderr, argv)
        self.assertFalse(ringer.amendments_path(self.log).exists())

    def test_amend_success_list_and_models_excluded_line(self) -> None:
        proc = self.ringer("amend", "r1", "t1", "--cause", "check", "--note", "check was broken", "--identity", "me")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("1, 2", proc.stdout)
        self.assertEqual(self.amendments()[0]["by"], "me")
        self.assertIsNone(self.amendments()[0]["attempt"])
        proc = self.ringer("amend", "r1", "t2", "--cause", "spec", "--note", "spec was impossible", "--attempt", "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.amendments()[1]["by"], "tester")

        listing = self.ringer("amend", "--list")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("all", listing.stdout)
        self.assertIn("spec", listing.stdout)
        self.assertEqual(len(self.ringer("amend", "--list", "--run", "other").stdout.splitlines()), 1)

        models = self.ringer("models", "--log", str(self.log), "--db", str(self.root / "x.db"))
        self.assertEqual(models.returncode, 0, models.stderr)
        lines = [ln for ln in models.stdout.splitlines() if ln.startswith("excluded from model stats")]
        self.assertEqual(
            lines,
            ["excluded from model stats: 3 attempt(s) (amended check: 2, spec: 1, infra: 0)"],
        )

    def test_models_prints_no_excluded_line_when_nothing_excluded(self) -> None:
        self.log.write_text(json.dumps(row("a", "PASS", "2026-01-01T00:00:01")) + "\n", encoding="utf-8")
        models = self.ringer("models", "--log", str(self.log), "--db", str(self.root / "x.db"))
        self.assertEqual(models.returncode, 0, models.stderr)
        self.assertNotIn("excluded from model stats", models.stdout)

    def test_from_file_is_all_or_nothing(self) -> None:
        good = {"run_id": "r1", "task_key": "t1", "attempt": 1, "cause": "check", "note": "check was broken", "by": "evil"}
        bad = {"run_id": "r1", "task_key": "ok", "attempt": 1, "cause": "check", "note": "check was broken"}
        short = {"run_id": "r1", "task_key": "t1", "attempt": None, "cause": "spec", "note": "no"}
        path = self.root / "batch.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in (good, bad, short)) + "\n", encoding="utf-8")
        proc = self.ringer("amend", "--from-file", str(path))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("line 2:", proc.stdout + proc.stderr)
        self.assertIn("line 3:", proc.stdout + proc.stderr)
        self.assertNotIn("line 1:", proc.stdout + proc.stderr)
        self.assertFalse(ringer.amendments_path(self.log).exists())

        path.write_text(json.dumps(good) + "\n", encoding="utf-8")
        proc = self.ringer("amend", "--from-file", str(path), "--identity", "batcher")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("1", proc.stdout)
        saved = self.amendments()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["by"], "batcher")
        self.assertNotIn("evil", json.dumps(saved))

    def test_jsonl_and_sqlite_paths_agree(self) -> None:
        proc = self.ringer("amend", "r1", "t1", "--cause", "check", "--note", "check was broken")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Non-default log path takes the JSONL branch; --db forces the SQLite branch.
        jsonl = self.ringer("models", "--json", "--log", str(self.log))
        sqlite = self.ringer("models", "--json", "--log", str(self.log), "--db", str(self.root / "x.db"))
        self.assertEqual(jsonl.returncode, 0, jsonl.stderr)
        self.assertEqual(sqlite.returncode, 0, sqlite.stderr)
        groups = json.loads(jsonl.stdout)
        self.assertEqual(groups, json.loads(sqlite.stdout))
        self.assertEqual(groups[0]["attempts"], 3)

    def test_api_payload_honours_amendments(self) -> None:
        before = ringer.build_models_api_payload(log_path=self.log, db_path=self.root / "api.db")
        proc = self.ringer("amend", "r1", "t1", "--cause", "check", "--note", "check was broken")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        after = ringer.build_models_api_payload(log_path=self.log, db_path=self.root / "api.db")
        self.assertLess(after["rollup"][0]["attempts"], before["rollup"][0]["attempts"])


if __name__ == "__main__":
    unittest.main()
