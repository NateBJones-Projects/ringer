"""The HTTP fields consumed by Referee must survive the Ringside endpoint."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["RINGER_NO_SELF_UPDATE"] = "1"
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ringer_referee_contract", ROOT / "ringer.py")
assert SPEC is not None and SPEC.loader is not None
ringer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ringer
SPEC.loader.exec_module(ringer)

# Keep in sync with every API field read by referee/hooks/core.ts.
NUMBER = (int, float)
FIELDS = {
    "run": {
        "run_id": (str,), "run_name": (str,), "started_at": (str,),
        "state": (str,), "max_parallel": (int,),
        "elapsed_s": NUMBER, "tokens": (*NUMBER, type(None)), "tasks": (list,),
    },
    "task": {
        "key": (str,), "status": (str,), "engine": (str,), "model": (str,),
        "activity": (str,), "attempts": (int,), "max_attempts": (int,), "check_output_tail": (str,),
        "elapsed_s": NUMBER, "tokens": (*NUMBER, type(None)),
    },
    "response": {"runs": (list,), "active": (dict,)},
}


class RefereeContractTests(unittest.TestCase):
    def assert_fields(self, kind, value):
        self.assertIsInstance(value, dict)
        for field, types in FIELDS[kind].items():
            with self.subTest(kind=kind, field=field):
                self.assertIn(field, value)
                self.assertIn(type(value[field]), types)

    def test_run_contract(self):
        fixture = json.loads((ROOT / "referee/tests/fixtures/api-runs-live.json").read_text())
        with tempfile.TemporaryDirectory(prefix="referee-contract-") as directory:
            state_dir = Path(directory)
            (state_dir / "runs").mkdir()
            run = fixture["runs"][0]
            (state_dir / "runs" / f'{run["run_id"]}.json').write_text(json.dumps(run))
            # Match test_active_runs.py's isolation of active_runs_path().
            with patch.dict(os.environ, {"RINGER_HOME": directory}):
                ringer.active_runs_path().write_text(json.dumps(fixture["active"]))
                response = {
                    "runs": ringer.scan_hud_run_states(state_dir),
                    "active": ringer.read_active_runs_file(),
                }
            self.assert_fields("response", response)
            self.assertEqual(len(response["runs"]), 1)
            self.assertIn(run["run_id"], response["active"])
            self.assertEqual(response["active"], fixture["active"])
            for actual in response["runs"]:
                self.assert_fields("run", actual)
                self.assertEqual(actual, run)
                self.assertTrue(actual["tasks"])
                for task in actual["tasks"]:
                    self.assert_fields("task", task)

    def test_mod_test_captures_match_read_only_fixtures(self):
        # claude-code/testing has no filesystem bottom or JSON module imports.
        # Verify its embedded HTTP responses against the original capture bytes.
        source = (ROOT / "referee/tests/referee.test.tsx").read_text()
        captures = re.findall(r'// Captured response: (\S+) .*\nconst \w+Text = (".*")\n', source)
        self.assertEqual({name for name, _ in captures},
                         {path.name for path in (ROOT / "referee/tests/fixtures").glob("api-runs-*.json")})
        self.assertEqual(len(captures), 6)
        for name, literal in captures:
            with self.subTest(fixture=name):
                self.assertEqual(json.loads(literal),
                                 (ROOT / "referee/tests/fixtures" / name).read_text())


if __name__ == "__main__":
    unittest.main()
