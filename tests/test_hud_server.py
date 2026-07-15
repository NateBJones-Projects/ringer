#!/usr/bin/env python3
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import PersistentHudServer, WORKER_LOG_TAIL_BYTES  # noqa: E402


class PersistentHudServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        self.root = Path(self.tmp.name)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.state_dir = self.root / "state"
        self.ringer_home = Path(os.environ["RINGER_HOME"])
        self.runs_dir = self.state_dir / "runs"
        self.artifacts_dir = self.state_dir / "artifacts"
        self.workdir = self.root / "work"
        self.runs_dir.mkdir(parents=True)
        self.artifacts_dir.mkdir(parents=True)
        self.workdir.mkdir(parents=True)
        self.ringside_stub = self.root / "ringside.html"
        self.ringside_stub.write_text("<!doctype html><main>stub ringside page</main>\n", encoding="utf-8")
        patcher = mock.patch.object(ringer, "RINGSIDE_HTML_PATH", self.ringside_stub)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.seed_state()

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def write_run(self, run_id: str, data: dict[str, object], mtime: float) -> None:
        path = self.runs_dir / f"{run_id}.json"
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.utime(path, (mtime, mtime))

    def seed_state(self) -> None:
        taskdir = self.workdir / "alpha"
        taskdir.mkdir(parents=True)
        self.log_tail = "T" * WORKER_LOG_TAIL_BYTES
        log_path = taskdir / "worker.log"
        log_path.write_bytes(b"old-prefix\n" + self.log_tail.encode("utf-8"))
        self.log_path = log_path

        live_run = {
            "run_id": "live-run",
            "run_name": "Live Run",
            "state": "live",
            "started_at": "2026-07-05T12:00:00+00:00",
            "tasks": [
                {
                    "key": "alpha",
                    "status": "running",
                    "taskdir": str(taskdir),
                    "log_path": str(log_path),
                }
            ],
        }
        finished_run = {
            "run_id": "finished-run",
            "run_name": "Finished Run",
            "state": "finished",
            "finished": True,
            "started_at": "2026-07-05T11:00:00+00:00",
            "tasks": [],
        }
        self.write_run("finished-run", finished_run, 1000.0)
        self.write_run("live-run", live_run, 2000.0)

        active = {
            "live-run": {
                "pid": os.getpid(),
                "identity": "test-agent",
                "run_name": "Live Run",
                "workdir": str(self.workdir),
                "started_at": "2026-07-05T12:00:00+00:00",
            }
        }
        self.ringer_home.mkdir(parents=True)
        (self.ringer_home / "active-runs.json").write_text(
            json.dumps(active, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        library = {
            "artifacts": {
                "Live Run": {
                    "current_run_id": "live-run",
                    "live_path": str(self.artifacts_dir / "live.html"),
                    "state": "live",
                    "versions": [],
                }
            }
        }
        (self.artifacts_dir / "library.json").write_text(
            json.dumps(library, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.artifacts_dir / "live.html").write_text("<h1>Live artifact</h1>\n", encoding="utf-8")

    def start_server(self) -> tuple[PersistentHudServer, int]:
        server = PersistentHudServer(self.state_dir, preferred_port=0, open_viewer=False)
        port = server.start()
        self.addCleanup(server.stop)
        return server, port

    def test_dead_live_run_is_reported_as_died(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.write_run("dead-run", {"run_id": "dead-run", "state": "live", "pid": proc.pid}, 3000.0)

        runs = ringer.scan_hud_run_states(self.state_dir)

        dead_run = next(item for item in runs if item["run_id"] == "dead-run")
        self.assertEqual("died", dead_run["state"])

    def test_live_run_with_alive_pid_stays_live(self) -> None:
        self.write_run("alive-run", {"run_id": "alive-run", "state": "live", "pid": os.getpid()}, 3000.0)

        runs = ringer.scan_hud_run_states(self.state_dir)

        alive_run = next(item for item in runs if item["run_id"] == "alive-run")
        self.assertEqual("live", alive_run["state"])

    def test_finished_run_is_passed_through_untouched(self) -> None:
        finished_run = {
            "run_id": "already-finished",
            "state": "finished",
            "finished": True,
            "pid": -1,
        }
        self.write_run("already-finished", finished_run, 3000.0)

        runs = ringer.scan_hud_run_states(self.state_dir)

        actual = next(item for item in runs if item["run_id"] == "already-finished")
        self.assertEqual(finished_run, actual)

    def test_live_run_without_usable_pid_stays_live(self) -> None:
        cases = {
            "missing-pid": {"run_id": "missing-pid", "state": "live"},
            "string-pid": {"run_id": "string-pid", "state": "live", "pid": "123"},
            "bool-pid": {"run_id": "bool-pid", "state": "live", "pid": False},
        }
        for index, (run_id, data) in enumerate(cases.items()):
            self.write_run(run_id, data, 3000.0 + index)

        runs = ringer.scan_hud_run_states(self.state_dir)

        states = {item["run_id"]: item["state"] for item in runs}
        for run_id in cases:
            with self.subTest(run_id=run_id):
                self.assertEqual("live", states[run_id])

    def test_reading_runs_does_not_modify_run_file(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.write_run("dead-run", {"run_id": "dead-run", "state": "live", "pid": proc.pid}, 3000.0)
        path = self.runs_dir / "dead-run.json"
        bytes_before = path.read_bytes()
        mtime_before = path.stat().st_mtime_ns

        runs = ringer.scan_hud_run_states(self.state_dir)

        dead_run = next(item for item in runs if item["run_id"] == "dead-run")
        self.assertEqual("died", dead_run["state"])
        self.assertEqual(bytes_before, path.read_bytes())
        self.assertEqual(mtime_before, path.stat().st_mtime_ns)

    def test_hud_serves_runs_library_artifacts_logs_and_ringside_page(self) -> None:
        _server, port = self.start_server()
        base = f"http://127.0.0.1:{port}"

        with urlopen(f"{base}/", timeout=5) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("text/html; charset=utf-8", response.headers["Content-Type"])
            self.assertIn("stub ringside page", response.read().decode("utf-8"))

        with urlopen(f"{base}/api/runs", timeout=5) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("application/json; charset=utf-8", response.headers["Content-Type"])
            self.assertEqual("no-store", response.headers["Cache-Control"])
            runs_body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(["live-run", "finished-run"], [item["run_id"] for item in runs_body["runs"]])
        self.assertEqual(os.getpid(), runs_body["active"]["live-run"]["pid"])

        with urlopen(f"{base}/api/library", timeout=5) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("no-store", response.headers["Cache-Control"])
            library_body = json.loads(response.read().decode("utf-8"))
        self.assertEqual("live", library_body["artifacts"]["Live Run"]["state"])

        with urlopen(f"{base}/artifacts/live.html", timeout=5) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("text/html; charset=utf-8", response.headers["Content-Type"])
            self.assertEqual("no-store", response.headers["Cache-Control"])
            self.assertIn(b"Live artifact", response.read())

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", "/artifacts/%2e%2e/runs/live-run.json")
        escape_response = conn.getresponse()
        self.assertEqual(404, escape_response.status)
        escape_response.read()

        with urlopen(f"{base}/logs/live-run/alpha", timeout=5) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("text/plain; charset=utf-8", response.headers["Content-Type"])
            self.assertEqual("no-store", response.headers["Cache-Control"])
            log_body = response.read().decode("utf-8")
        self.assertEqual(self.log_tail, log_body)
        self.assertNotIn("old-prefix", log_body)

        conn.request("GET", "/logs/live-run/unknown")
        unknown_task = conn.getresponse()
        self.assertEqual(404, unknown_task.status)
        unknown_task.read()

        conn.request("GET", "/logs/unknown-run/alpha")
        unknown_run = conn.getresponse()
        self.assertEqual(404, unknown_run.status)
        unknown_run.read()


if __name__ == "__main__":
    unittest.main(verbosity=2)
