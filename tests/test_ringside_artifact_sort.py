#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "dashboard" / "ringside.html"

NODE = shutil.which("node")

LIB_START = "    function numberOrZero(value) {"
LIB_END = "    function sanitizeArtifactName(value) {"

HELPERS = [
    "numberOrZero",
    "parseTime",
    "normalizeArtifactState",
    "artifactVersionKey",
    "normalizeLibrary",
]


def slice_production_js(html: str) -> str:
    start = html.index(LIB_START)
    end = html.index(LIB_END)
    body = html[start:end]
    return body + "\nmodule.exports = { " + ", ".join(HELPERS) + " };\n"


@unittest.skipIf(NODE is None, "Node.js not available")
class NormalizeLibraryArtifactSortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        html = HTML_PATH.read_text(encoding="utf-8")
        js = slice_production_js(html)
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False, encoding="utf-8"
        )
        tmp.write(js)
        tmp.close()
        cls._tmp_path = tmp.name
        wrapper = tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False, encoding="utf-8"
        )
        wrapper.write(f"const m = require({json.dumps(tmp.name)});\n")
        wrapper.write("const data = JSON.parse(process.argv[2]);\n")
        wrapper.write("process.stdout.write(JSON.stringify(m.normalizeLibrary(data)));\n")
        wrapper.close()
        cls._wrapper_path = wrapper.name

    @classmethod
    def tearDownClass(cls) -> None:
        for path in (getattr(cls, "_tmp_path", None), getattr(cls, "_wrapper_path", None)):
            if path and os.path.exists(path):
                os.remove(path)

    def _run(self, payload: dict) -> list[dict]:
        proc = subprocess.run(
            [NODE, self._wrapper_path, json.dumps(payload)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def _names(self, payload: dict) -> list[str]:
        return [item["name"] for item in self._run(payload)]

    def _artifact(self, name: str, state: str, updated_at: str = "", versions=None) -> dict:
        entry = {"state": state, "updated_at": updated_at}
        if versions is not None:
            entry["versions"] = versions
        return entry

    def test_live_first_stable_alphabetical_across_leapfrogs(self) -> None:
        payload = {
            "artifacts": {
                "Bravo": self._artifact("Bravo", "live", "2026-07-01T10:00:00Z"),
                "Alpha": self._artifact("Alpha", "live", "2026-07-18T10:00:00Z"),
                "Zeta": self._artifact("Zeta", "pass", "2026-07-10T10:00:00Z"),
            }
        }
        first = self._names(payload)
        self.assertEqual(first[:2], ["Alpha", "Bravo"])
        # Leapfrog: Alpha's timestamp now older than Bravo's.
        payload["artifacts"]["Alpha"]["updated_at"] = "2026-07-01T09:00:00Z"
        payload["artifacts"]["Bravo"]["updated_at"] = "2026-07-18T11:00:00Z"
        second = self._names(payload)
        self.assertEqual(second[:2], ["Alpha", "Bravo"])
        self.assertEqual(second[-1], "Zeta")

    def test_nonlive_descending_timestamps_across_mixed_states(self) -> None:
        payload = {
            "artifacts": {
                "Old": self._artifact("Old", "pass", "2026-07-01T10:00:00Z"),
                "New": self._artifact("New", "fail", "2026-07-15T10:00:00Z"),
                "Mid": self._artifact("Mid", "died", "2026-07-08T10:00:00Z"),
            }
        }
        self.assertEqual(self._names(payload), ["New", "Mid", "Old"])

    def test_invalid_missing_updated_at_falls_back_to_newest_version_finished_at(self) -> None:
        versions_old = [{"run_id": "r1", "finished_at": "2026-07-01T10:00:00Z", "outcome": "pass"}]
        versions_new = [{"run_id": "r2", "finished_at": "2026-07-12T10:00:00Z", "outcome": "pass"}]
        payload = {
            "artifacts": {
                "InvalidTs": self._artifact("InvalidTs", "pass", "not-a-date", versions_old),
                "MissingTs": self._artifact("MissingTs", "fail", "", versions_new),
            }
        }
        names = self._names(payload)
        # Missing/invalid updated_at → fallback finished_at → New (Jul 12) before Old (Jul 1).
        self.assertEqual(names, ["MissingTs", "InvalidTs"])

    def test_equal_timestamp_name_ordering(self) -> None:
        shared = "2026-07-10T10:00:00Z"
        payload = {
            "artifacts": {
                "Zulu": self._artifact("Zulu", "pass", shared),
                "Yankee": self._artifact("Yankee", "fail", shared),
                "Xray": self._artifact("Xray", "died", shared),
            }
        }
        self.assertEqual(self._names(payload), ["Xray", "Yankee", "Zulu"])

    def test_zero_timestamp_name_ordering(self) -> None:
        payload = {
            "artifacts": {
                "Charlie": self._artifact("Charlie", "pass", "not-a-date"),
                "Bravo": self._artifact("Bravo", "pass", "not-a-date"),
                "Alpha": self._artifact("Alpha", "pass", "not-a-date"),
            }
        }
        self.assertEqual(self._names(payload), ["Alpha", "Bravo", "Charlie"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
