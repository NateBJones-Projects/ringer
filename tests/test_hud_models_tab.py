#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import MODEL_SCOREBOARD_COLUMNS, PersistentHudServer  # noqa: E402


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def attempt(
    *,
    run_id: str,
    task_key: str,
    task_type: str,
    verdict: str = "PASS",
    retry: bool = False,
    logged_at: str = "2026-07-06T10:00:00+00:00",
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "task_key": task_key,
        "worker_engine": "opencode",
        "model": "openrouter/acme/small",
        "task_type": task_type,
        "verdict": verdict,
        "retry": retry,
        "duration_ms": 120,
        "worker_tokens": 240,
        "logged_at": logged_at,
        "orchestrator": "tester",
    }


class HudModelsTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        self.root = Path(self.tmp.name)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.state_dir = self.root / "state"
        self.state_dir.mkdir(parents=True)
        self.log_path = self.root / "models.jsonl"
        self.db_path = self.root / "ringer.db"

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def start_server(self, *, use_db: bool = True) -> tuple[PersistentHudServer, int]:
        server = PersistentHudServer(self.state_dir, preferred_port=0, open_viewer=False)
        server.model_log_path = self.log_path
        if use_db:
            server.model_db_path = self.db_path
        port = server.start_background()
        self.addCleanup(server.stop)
        return server, port

    def test_api_models_serves_contract_from_override_log(self) -> None:
        write_jsonl(
            self.log_path,
            [
                attempt(run_id="run-1", task_key="a", task_type="code-feature"),
                attempt(run_id="run-2", task_key="b", task_type="research", verdict="FAIL"),
                attempt(run_id="run-2", task_key="b", task_type="research", retry=True),
                attempt(run_id="run-3", task_key="c", task_type="code-feature", logged_at="2026-07-05T10:00:00+00:00"),
            ],
        )
        _server, port = self.start_server()

        with urlopen(f"http://127.0.0.1:{port}/api/models", timeout=5) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("application/json; charset=utf-8", response.headers["Content-Type"])
            payload = json.loads(response.read().decode("utf-8"))

        self.assertIn("generated_at", payload)
        self.assertIn("groups", payload)
        self.assertIn("rollup", payload)
        self.assertNotIn("error", payload)
        groups_by_type = {group["task_type"]: group for group in payload["groups"]}
        self.assertEqual({"code-feature", "research"}, set(groups_by_type))
        self.assertEqual(2, groups_by_type["code-feature"]["tasks"])
        self.assertEqual(list(MODEL_SCOREBOARD_COLUMNS), payload["columns"])
        self.assertEqual("Small", groups_by_type["code-feature"]["model_display"])
        self.assertEqual("OpenCode", groups_by_type["code-feature"]["harness"])
        self.assertEqual("OpenRouter API", groups_by_type["code-feature"]["access"])

        self.assertEqual(1, len(payload["rollup"]))
        rollup = payload["rollup"][0]
        self.assertEqual("openrouter/acme/small", rollup["model"])
        self.assertEqual("Small", rollup["model_display"])
        self.assertEqual("proven", rollup["tier"])
        self.assertEqual(3, rollup["tasks"])
        self.assertEqual(4, rollup["attempts"])
        self.assertEqual(1.0, rollup["pass_rate"])
        self.assertAlmostEqual(2 / 3, rollup["first_try_pass_rate"])
        self.assertEqual("2026-07-06T10:00:00+00:00", rollup["last_seen"])

    def test_api_models_override_log_without_db_does_not_touch_default_db(self) -> None:
        write_jsonl(self.log_path, [attempt(run_id="run-1", task_key="a", task_type="code-feature")])
        default_db = Path(os.environ["RINGER_HOME"]) / "ringer.db"
        _server, port = self.start_server(use_db=False)

        with urlopen(f"http://127.0.0.1:{port}/api/models", timeout=5) as response:
            self.assertEqual(200, response.status)
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(1, len(payload["groups"]))
        self.assertNotIn("error", payload)
        self.assertFalse(default_db.exists())
        self.assertFalse(default_db.with_name(default_db.name + "-wal").exists())
        self.assertFalse(default_db.with_name(default_db.name + "-shm").exists())

    def test_api_models_error_path_returns_200(self) -> None:
        bad_parent = self.root / "not-a-directory"
        bad_parent.write_text("file", encoding="utf-8")
        self.log_path = bad_parent / "models.jsonl"
        _server, port = self.start_server()

        with urlopen(f"http://127.0.0.1:{port}/api/models", timeout=5) as response:
            self.assertEqual(200, response.status)
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual([], payload["groups"])
        self.assertEqual([], payload["rollup"])
        self.assertIn("error", payload)
        self.assertIn("generated_at", payload)

    def test_frontend_sources_include_models_tab_and_api_fetch(self) -> None:
        dashboard_html = (ROOT / "dashboard" / "dashboard.html").read_text(encoding="utf-8")
        hud_js = (ROOT / "hud" / "frontend" / "hud.js").read_text(encoding="utf-8")

        self.assertIn('data-view="models"', dashboard_html)
        self.assertIn("Models", dashboard_html)
        self.assertTrue("/api/models" in dashboard_html or "/api/models" in hud_js)

    def test_start_background_returns_usable_port(self) -> None:
        write_jsonl(self.log_path, [attempt(run_id="run-1", task_key="a", task_type="code-feature")])
        _server, port = self.start_server()

        self.assertIsInstance(port, int)
        self.assertGreater(port, 0)
        with urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            page = response.read().decode("utf-8")
        self.assertIn('id="models-tab"', page)
        self.assertIn("/api/models", page)
        for column in MODEL_SCOREBOARD_COLUMNS:
            self.assertIn(f">{column}<", page)
        with urlopen(f"http://127.0.0.1:{port}/api/models", timeout=5) as response:
            self.assertEqual(200, response.status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
