#!/usr/bin/env python3
from __future__ import annotations

import http.client
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    PersistentHudServer,
)


class HudPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.config_path = self.root / "config.toml"
        self.config_path.write_text("# test config\n", encoding="utf-8")
        self.config = AppConfig(
            path=self.config_path,
            identity_default="plan-test",
            state_dir=self.state_dir,
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=self.state_dir / "runs.jsonl"),
            engines={
                "codex": EngineConfig(
                    name="codex",
                    bin=sys.executable,
                    args_template=("exec", "{engine_args}", "-m", "{model}", "-C", "{taskdir}", "{spec}"),
                    full_access_args=(),
                    sandbox_args=(),
                    token_regex=None,
                    model_default="gpt-5.5",
                ),
                "opencode": EngineConfig(
                    name="opencode",
                    bin=sys.executable,
                    args_template=("run", "-m", "{model}", "--dir", "{taskdir}", "{spec}"),
                    full_access_args=(),
                    sandbox_args=(),
                    token_regex=None,
                    model_default="openrouter/z-ai/glm-5.2",
                ),
            },
            artifact=ArtifactConfig(
                enabled=True,
                out_template=str(self.state_dir / "artifacts" / "{run_id}.html"),
                report_template=str(self.state_dir / "artifacts" / "{run_id}-report.html"),
                index_out=self.state_dir / "artifacts" / "index.html",
            ),
        )
        self.server = PersistentHudServer(
            self.state_dir,
            preferred_port=0,
            open_viewer=False,
            config=self.config,
        )
        self.port = self.server.start()
        self.addCleanup(self.server.stop)

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        return response.status, data

    def manifest(self, *, model: str = "gpt-5.5") -> dict[str, object]:
        return {
            "orch_task_id": "8e748b50-f5dc-48e8-bcb5-c193d4c95664",
            "run_name": "plan-test",
            "workdir": str(self.root / "work"),
            "max_parallel": 2,
            "worktrees": False,
            "tasks": [
                {
                    "key": "implementation",
                    "task_type": "code-feature",
                    "engine": "codex",
                    "model": model,
                    "engine_args": ["-c", "model_reasoning_effort=medium"],
                    "spec": (
                        "Create output.md in the task directory with the requested implementation notes, "
                        "keep the work scoped to that file, and include a final verification section."
                    ),
                    "check": (
                        "test -s output.md || { echo 'FAIL: output.md is missing or empty'; exit 1; }"
                    ),
                    "verified": "output.md exists and contains the requested implementation notes",
                    "expect_files": ["output.md"],
                }
            ],
        }

    def test_plan_ui_and_policy_are_served(self) -> None:
        status, payload = self.request_json("GET", "/api/plan")
        self.assertEqual(200, status)
        lanes = {str(item["id"]): item for item in payload["lanes"]}  # type: ignore[index]
        self.assertFalse(lanes["standard"]["premium"])
        self.assertTrue(lanes["gpt-5.6-terra"]["premium"])
        self.assertEqual("gpt-5.5", lanes["standard"]["model"])

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", "/")
        response = conn.getresponse()
        page = response.read().decode("utf-8")
        self.assertEqual(200, response.status)
        self.assertIn('id="plan-tab"', page)
        self.assertIn('id="plan-form"', page)
        self.assertIn('id="plan-orch-task-id"', page)
        self.assertIn('new URLSearchParams(window.location.search)', page)
        self.assertIn('has("orch_task_id")', page)
        self.assertIn('window.location.hash.replace(/^#/', page)
        self.assertIn('agentops_autostart', page)
        self.assertIn('manifest.proof_kinds', page)
        self.assertIn('Starting the linked task', page)
        self.assertIn("FAIL: verify.sh is missing or not executable", page)
        self.assertIn('/api/plan/validate', page)
        self.assertIn('/api/plan/run', page)

    def test_validate_requires_an_explicit_task_model(self) -> None:
        manifest = self.manifest()
        manifest["tasks"][0]["model"] = ""  # type: ignore[index]
        status, payload = self.request_json(
            "POST",
            "/api/plan/validate",
            {"manifest": manifest},
        )
        self.assertEqual(400, status)
        self.assertIn("choose an explicit model", str(payload["error"]))

    def test_premium_plan_validates_but_cannot_launch_without_approval(self) -> None:
        body = {"manifest": self.manifest(model="gpt-5.6-terra")}
        status, payload = self.request_json("POST", "/api/plan/validate", body)
        self.assertEqual(200, status)
        self.assertFalse(payload["ready"])
        self.assertEqual(["implementation"], payload["routing"]["premium_tasks"])  # type: ignore[index]

        status, payload = self.request_json("POST", "/api/plan/run", body)
        self.assertEqual(400, status)
        self.assertIn("premium routing is not approved", str(payload["error"]))

    def test_standard_plan_launches_and_persists_routing_receipt(self) -> None:
        process = mock.Mock(pid=4812)
        with mock.patch.object(ringer.subprocess, "Popen", return_value=process) as popen:
            status, payload = self.request_json(
                "POST",
                "/api/plan/run",
                {"manifest": self.manifest(), "premium_approved": False, "premium_reason": ""},
            )

        self.assertEqual(202, status)
        self.assertEqual(4812, payload["pid"])
        manifest_path = Path(str(payload["manifest_path"]))
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("8e748b50-f5dc-48e8-bcb5-c193d4c95664", saved["orch_task_id"])
        self.assertEqual("gpt-5.5", saved["routing"]["task_models"]["implementation"]["model"])
        self.assertFalse(saved["routing"]["premium_approved"])
        command = popen.call_args.args[0]
        self.assertEqual(sys.executable, command[0])
        self.assertIn(str(manifest_path), command)

    def test_agentops_autostart_persists_lifecycle_contract(self) -> None:
        process = mock.Mock(pid=4813)
        body = {
            "manifest": {
                **self.manifest(),
                "proof_kinds": ["test", "artifact"],
            },
            "agentops_autostart": True,
            "premium_approved": False,
            "premium_reason": "",
        }
        with mock.patch.object(ringer.subprocess, "Popen", return_value=process):
            status, payload = self.request_json("POST", "/api/plan/run", body)

        self.assertEqual(202, status)
        saved = json.loads(
            Path(str(payload["manifest_path"])).read_text(encoding="utf-8")
        )
        self.assertTrue(saved["agentops_autostart"])
        self.assertEqual(["test", "artifact"], saved["proof_kinds"])

    def test_invalid_agentops_task_id_is_rejected(self) -> None:
        manifest = self.manifest()
        manifest["orch_task_id"] = "not-a-task-id"
        status, payload = self.request_json(
            "POST",
            "/api/plan/validate",
            {"manifest": manifest},
        )
        self.assertEqual(400, status)
        self.assertIn("orch_task_id must be a valid UUID", str(payload["error"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
