#!/usr/bin/env python3
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    Dashboard,
    EngineConfig,
    StateWriter,
    TaskRuntime,
    TaskSpec,
    WORKER_LOG_TAIL_BYTES,
)


class LogEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        self.root = Path(self.tmp.name)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.state_dir = self.root / "state"
        self.workdir = self.root / "work"
        self.engine = EngineConfig(
            name="mock",
            bin=sys.executable,
            args_template=("-c", "pass"),
            full_access_args=(),
            sandbox_args=(),
        )

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def runtime(self, key: str = "known-task") -> TaskRuntime:
        taskdir = self.workdir / key
        taskdir.mkdir(parents=True, exist_ok=True)
        log_path = taskdir / "worker.log"
        log_path.write_bytes(b"old-prefix\n" + (b"T" * WORKER_LOG_TAIL_BYTES))
        runtime = TaskRuntime(
            task=TaskSpec(
                key=key,
                spec="Write the requested file.",
                check="test -s worker.log || { echo FAIL: missing log; exit 1; }",
                engine="mock",
            ),
            taskdir=taskdir,
            log_path=log_path,
            status="running",
            attempts=1,
            spec_short="write file",
        )
        runtime.started_at_monotonic = 1.0
        return runtime

    def writer(self, runtime: TaskRuntime) -> StateWriter:
        return StateWriter(
            "run-logs",
            "Log Endpoint Run",
            "test-agent",
            self.state_dir,
            {"mock": self.engine},
            datetime(2026, 7, 5, tzinfo=timezone.utc),
            [runtime],
            threading.RLock(),
        )

    def test_dashboard_serves_worker_log_tail_by_task_key_only(self) -> None:
        writer = self.writer(self.runtime())
        writer.start()
        self.addCleanup(writer.stop)

        initial_state = json.loads(writer.path.read_text(encoding="utf-8"))
        self.assertIn("dashboard_port", initial_state)
        self.assertIsNone(initial_state["dashboard_port"])

        dashboard = Dashboard(
            state_path=writer.path,
            preferred_port=0,
            open_viewer=False,
        )
        try:
            port = dashboard.start()
        except ringer.BindNotPermittedError:
            self.skipTest("environment forbids socket binding")
        self.addCleanup(dashboard.stop)
        writer.set_port(port)

        with urlopen(f"http://127.0.0.1:{port}/state.json", timeout=5) as response:
            self.assertEqual(200, response.status)
            state_body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(port, state_body["dashboard_port"])

        with urlopen(f"http://127.0.0.1:{port}/logs/known-task", timeout=5) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("text/plain; charset=utf-8", response.headers["Content-Type"])
            body = response.read().decode("utf-8")
        self.assertEqual("T" * WORKER_LOG_TAIL_BYTES, body)
        self.assertNotIn("old-prefix", body)

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.addCleanup(conn.close)

        conn.request("GET", "/logs/unknown")
        unknown_response = conn.getresponse()
        self.assertEqual(404, unknown_response.status)
        unknown_response.read()

        conn.request("GET", "/logs/../x")
        traversal_response = conn.getresponse()
        self.assertEqual(404, traversal_response.status)
        traversal_response.read()


if __name__ == "__main__":
    unittest.main(verbosity=2)
