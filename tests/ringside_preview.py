"""Disposable Ringside browser fixture; never launches workers or uses real run data."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ringer import PersistentHudServer  # noqa: E402


def seed(root: Path) -> None:
    now = datetime.now(timezone.utc)
    artifacts = root / "artifacts"
    (artifacts / "live").mkdir(parents=True)
    (artifacts / "versions").mkdir()
    (root / "runs").mkdir()
    tasks = []
    for index, (key, status, engine) in enumerate([
        ("Check authentication", "pass", "codex"),
        ("Repair redirect behavior", "retrying", "opencode"),
        ("Update landing page", "running", "codex"),
        ("Validate permissions", "verifying", "grok"),
        ("Check error messages", "pass", "codex"),
        ("Review accessibility", "pass", "opencode"),
        ("Publish result", "pending", "codex"),
    ]):
        log = root / f"worker-{index}.log"
        log.write_text("Checking local fixture…\nFAIL: expected /dashboard, received /login\n", encoding="utf-8")
        tasks.append({"key":key, "status":status, "engine":engine,
                      "model":"Fixture model", "elapsed_s":60+index*32,
                      "attempts":2 if status == "retrying" else 1,
                      "max_attempts":3, "check_returncode":1 if status == "retrying" else 0 if status == "pass" else None,
                      "check_output_tail":"FAIL: expected /dashboard, received /login" if status == "retrying" else "PASS: executed check" if status == "pass" else "",
                      "check":"python3 checks/redirect_check.py", "spec":"Local fixture: verify the redirect behavior.", "log_path":str(log)})
    runs = [
        {"run_id":"release", "run_name":"Website release", "state":"live", "finished":False, "tasks":tasks},
        {"run_id":"docs", "run_name":"Documentation audit", "state":"live", "finished":False, "tasks":[dict(tasks[0]), dict(tasks[2])]},
        {"run_id":"stopped", "run_name":"Checkout regression", "state":"died", "finished":False, "tasks":[dict(tasks[2])]},
        {"run_id":"done", "run_name":"API contract review", "state":"finished", "finished":True, "tasks":[dict(tasks[0])]},
    ]
    for run in runs:
        run.update({"identity":"local fixture", "started_at":(now-timedelta(minutes=9)).isoformat(), "pid":os.getpid() if run["state"] == "live" else 99999999})
        (root / "runs" / f"{run['run_id']}.json").write_text(json.dumps(run), encoding="utf-8")
    registry = root / "ringer-home"
    registry.mkdir()
    (registry / "active-runs.json").write_text(json.dumps({run["run_id"]:{"pid":os.getpid(), "run_name":run["run_name"], "workdir":str(root), "started_at":run["started_at"]} for run in runs if run["state"] == "live"}), encoding="utf-8")
    library = {"artifacts":{}}
    for name, slug in [("Website release", "Website-release"), ("Documentation audit", "Documentation-audit")]:
        live = artifacts / "live" / f"{slug}.html"
        old = artifacts / "versions" / f"{slug}-v1.html"
        style = '<style>body{background:#121315;color:#f3f4f6;font:15px/1.5 system-ui;padding:30px}h1{font-size:30px}p{color:#a8abb5}li{margin:22px 0}</style>'
        live.write_text(f'{style}<p>LOCAL TEST FIXTURE · {name}</p><h1>The release is taking shape.</h1><p>3 checks passed. Two tasks are working.<br>One task is retrying after a redirect check failed.</p><hr><h2>Ready to review</h2><ul><li>Authentication</li><li>Error messages</li><li>Accessibility notes</li></ul>', encoding="utf-8")
        old.write_text(f'{style}<h1>Saved version 1</h1><p>This historical output must stay selected during polling.</p>', encoding="utf-8")
        run_id = "release" if slug == "Website-release" else "docs"
        library["artifacts"][name] = {"state":"live", "identity":"local fixture", "current_run_id":run_id, "live_path":str(live), "updated_at":now.isoformat(), "versions":[{"path":str(old), "run_id":"earlier", "finished_at":(now-timedelta(hours=1)).isoformat(), "outcome":"pass"}]}
    (artifacts / "library.json").write_text(json.dumps(library), encoding="utf-8")
    rows = []
    for engine, model, count in [("codex","fixture/model-a",8),("opencode","fixture/model-b",2)]:
        for index in range(count):
            rows.append({"run_id":f"fixture-{engine}-{index}", "task_key":str(index), "worker_engine":engine, "model":model, "task_type":"code-feature" if index%2 == 0 else "research", "verdict":"PASS" if index != 3 else "FAIL", "retry":False, "duration_ms":128000, "worker_tokens":1200, "logged_at":now.isoformat()})
    (root / "fixture-models.jsonl").write_text("".join(json.dumps(row)+"\n" for row in rows), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--empty", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ringside-preview-") as temp:
        root = Path(temp)
        # Isolate the active-run registry and scoreboard; no live state is touched.
        os.environ["RINGER_HOME"] = str(root / "ringer-home")
        if not args.empty:
            seed(root)
        server = PersistentHudServer(root, preferred_port=args.port, open_viewer=False)
        server.model_log_path = root / "fixture-models.jsonl"
        server.model_db_path = root / "fixture.db"
        server.model_notes_path = root / "no-notes.md"
        port = server.start()
        print(f"Local test fixture only: http://127.0.0.1:{port}", flush=True)
        stop = threading.Event()
        def heartbeat() -> None:
            while not stop.wait(1):
                for path in (root / "runs").glob("*.json"):
                    path.touch()
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            stop.wait()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            thread.join()
            server.stop()


if __name__ == "__main__":
    main()
