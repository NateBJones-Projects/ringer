#!/usr/bin/env python3
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    HUD_PLAN_BODY_LIMIT_BYTES,
    PersistentHudServer,
)


LONG_SPEC = (
    "Implement the requested scoped change while preserving unrelated user changes. "
    "Keep edits bounded to the specified ownership paths and record the exact verification evidence."
)
GOOD_CHECK = "test -s report.md || { echo 'FAIL: report.md missing or empty'; exit 1; }"


class HudPlanTests(unittest.TestCase):
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
        self.config = self.make_config()

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def make_config(self) -> AppConfig:
        return AppConfig(
            path=None,
            identity_default=None,
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
                    args_template=(
                        "exec",
                        "--skip-git-repo-check",
                        "{model_args}",
                        "{engine_args}",
                        "-C",
                        "{taskdir}",
                        "{spec}",
                    ),
                    full_access_args=(),
                    sandbox_args=("--sandbox", "workspace-write"),
                    token_regex=None,
                    model_default="gpt-5.5",
                )
            },
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(self.state_dir / "live.html"),
                report_template=str(self.state_dir / "report.html"),
                index_out=self.state_dir / "index.html",
            ),
        )

    def start_server(self) -> tuple[PersistentHudServer, str]:
        server = PersistentHudServer(
            self.state_dir,
            preferred_port=0,
            open_viewer=False,
            config=self.config,
        )
        port = server.start_background()
        self.addCleanup(server.stop)
        return server, f"http://127.0.0.1:{port}"

    def post_json(self, base: str, path: str, body: object) -> tuple[int, dict[str, object]]:
        data = json.dumps(body).encode("utf-8")
        request = Request(
            f"{base}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def manifest(self, *, model: str = "gpt-5.5", task_key: str = "alpha") -> dict[str, object]:
        return {
            "run_name": "hud-plan-test",
            "workdir": str(self.root / "work"),
            "max_parallel": 2,
            "tasks": [
                {
                    "key": task_key,
                    "spec": LONG_SPEC,
                    "check": GOOD_CHECK,
                    "expect_files": ["report.md"],
                    "engine": "codex",
                    "model": model,
                    "task_type": "code-feature",
                    "engine_args": ["-c", "model_reasoning_effort=medium"],
                    "verified": "report.md exists and the explicit check passed.",
                }
            ],
        }

    def test_served_ui_and_policy_include_plan_run_and_exact_gpt56_lanes(self) -> None:
        _server, base = self.start_server()

        with urlopen(f"{base}/", timeout=5) as response:
            page = response.read().decode("utf-8")
        self.assertIn('id="plan-tab"', page)
        self.assertIn("Plan / Run", page)
        self.assertIn("/api/plan/validate", page)
        self.assertIn("/api/plan/run", page)

        with urlopen(f"{base}/api/plan", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        lane_models = {lane["model"] for lane in payload["lanes"]}
        self.assertIn("gpt-5.5", lane_models)
        self.assertIn("gpt-5.6-terra", lane_models)
        self.assertIn("gpt-5.6-sol", lane_models)
        self.assertIn("gpt-5.6-luna", lane_models)
        self.assertEqual(
            ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"],
            payload["policy"]["premium_models"],
        )
        self.assertTrue(payload["policy"]["explicit_task_models"])
        self.assertFalse(payload["policy"]["agentops_linkage"])
        self.assertFalse(payload["policy"]["auto_start"])

    def test_validate_requires_explicit_model_for_plan_tasks(self) -> None:
        _server, base = self.start_server()
        manifest = self.manifest()
        manifest["tasks"][0]["model"] = ""

        status, payload = self.post_json(base, "/api/plan/validate", {"manifest": manifest})

        self.assertEqual(400, status)
        self.assertIn("choose an explicit model", str(payload["error"]))

    def test_premium_validation_reports_routing_but_launch_requires_approval_and_reason(self) -> None:
        _server, base = self.start_server()
        request = {"manifest": self.manifest(model="gpt-5.6-terra")}

        status, payload = self.post_json(base, "/api/plan/validate", request)
        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])
        self.assertEqual(["alpha"], payload["routing"]["premium_tasks"])
        self.assertFalse(payload["ready"])

        status, payload = self.post_json(base, "/api/plan/run", request)
        self.assertEqual(400, status)
        self.assertIn("premium routing is not approved", str(payload["error"]))

        status, payload = self.post_json(
            base,
            "/api/plan/run",
            {"manifest": self.manifest(model="gpt-5.6-sol"), "premium_approved": True},
        )
        self.assertEqual(400, status)
        self.assertIn("premium routing needs a short reason", str(payload["error"]))

    def test_standard_launch_persists_receipt_and_invokes_cli_wrapper(self) -> None:
        _server, base = self.start_server()
        with mock.patch.object(
            ringer,
            "launch_hud_plan",
            return_value=SimpleNamespace(pid=4242),
        ) as launch:
            status, payload = self.post_json(base, "/api/plan/run", {"manifest": self.manifest()})

        self.assertEqual(202, status)
        self.assertTrue(payload["ok"])
        self.assertEqual(4242, payload["pid"])
        manifest_path = Path(str(payload["manifest_path"]))
        self.assertTrue(manifest_path.is_file())
        self.assertEqual(self.state_dir / "plans", manifest_path.parent)
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("gpt-5.5", saved["tasks"][0]["model"])
        self.assertEqual("codex", saved["tasks"][0]["engine"])
        self.assertEqual("gpt-5.5", saved["routing"]["task_models"]["alpha"]["model"])
        self.assertEqual([], saved["routing"]["premium_tasks"])
        launch.assert_called_once()
        self.assertEqual(manifest_path, launch.call_args.args[1])

    def test_malformed_unsupported_and_oversized_requests_return_useful_errors(self) -> None:
        _server, base = self.start_server()

        request = Request(
            f"{base}/api/plan/validate",
            data=b"{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as invalid_json:
            urlopen(request, timeout=5)
        self.assertEqual(400, invalid_json.exception.code)
        self.assertIn("invalid JSON", invalid_json.exception.read().decode("utf-8"))

        request = Request(
            f"{base}/api/plan/validate",
            data=b"{}",
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as bad_type:
            urlopen(request, timeout=5)
        self.assertEqual(415, bad_type.exception.code)
        self.assertIn("Content-Type must be application/json", bad_type.exception.read().decode("utf-8"))

        conn = http.client.HTTPConnection(base.removeprefix("http://"), timeout=5)
        self.addCleanup(conn.close)
        conn.request(
            "POST",
            "/api/plan/validate",
            body=b"x" * (HUD_PLAN_BODY_LIMIT_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        self.assertEqual(413, response.status)
        self.assertIn("request body is too large", response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
