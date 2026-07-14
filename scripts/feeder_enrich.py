#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Shared aggregation lives in feeder_agg (single source of truth, also used by
# ringer.py's live /live-model route). scripts/ is on sys.path when run as a script;
# insert it defensively so imports work regardless of invocation cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feeder_agg


def main():
    parser = argparse.ArgumentParser(description="Enrich Ringer run state with Feeder telemetry")
    parser.add_argument("--state", required=True, help="Path to run state JSON")
    parser.add_argument("--workdir", required=True, help="Path to task work directory")
    parser.add_argument("--fixture", help="Path to fixture JSON mapping session_id -> [rows]")
    parser.add_argument("--feeder-base", default="http://localhost:3001", help="Feeder base URL")
    parser.add_argument("--rerender", action="store_true", help="Re-render the artifact pages after enrichment")
    parser.add_argument("--artifacts-dir", default=os.path.expanduser("~/.ringer/artifacts"), help="Path to artifacts directory (default: ~/.ringer/artifacts)")
    args = parser.parse_args()

    try:
        with open(args.state, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error: cannot read state JSON: {e}")
        return 1

    if "tasks" not in state:
        print("Error: state JSON missing 'tasks' field")
        return 1

    fixture_rows = None
    if args.fixture:
        try:
            with open(args.fixture, "r", encoding="utf-8") as f:
                fixture_rows = json.load(f)
            if not isinstance(fixture_rows, dict):
                print("Error: fixture JSON must be an object mapping session_id -> rows")
                return 1
        except (OSError, json.JSONDecodeError) as e:
            print(f"Error: cannot read fixture JSON: {e}")
            return 1

    for task in state["tasks"]:
        task_key = task.get("key")
        if not task_key:
            continue

        # non-worktrees layout first; worktrees mode keeps logs outside the
        # (deleted-on-PASS) task worktree, under <workdir>/logs/
        candidate_logs = [
            os.path.join(args.workdir, task_key, "worker.log"),
            os.path.join(args.workdir, "logs", f"{task_key}.worker.log"),
        ]
        log_content = None
        for worker_log_path in candidate_logs:
            try:
                with open(worker_log_path, "r", encoding="utf-8") as f:
                    log_content = f.read()
                break
            except OSError:
                continue
        if log_content is None:
            print(f"Note: task {task_key}: worker.log missing, skipping")
            continue

        session_ids = []
        for match in re.finditer(r'"sessionID":"(ses_[^"]+)"', log_content):
            sid = match.group(1)
            if sid not in session_ids:
                session_ids.append(sid)

        if not session_ids:
            print(f"Note: task {task_key}: no session IDs found, skipping")
            continue

        all_rows = []
        for sid in session_ids:
            if fixture_rows is not None:
                rows = fixture_rows.get(sid, [])
            else:
                try:
                    url = f"{args.feeder_base}/api/requests?session_id={sid}"
                    req = Request(url, headers={"Accept": "application/json"})
                    with urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        if isinstance(data, dict) and "requests" in data:
                            rows = data["requests"]
                        elif isinstance(data, list):
                            rows = data
                        else:
                            rows = []
                except (HTTPError, URLError, json.JSONDecodeError, OSError) as e:
                    print(f"Note: task {task_key}: failed to fetch session {sid}: {e}, skipping task")
                    all_rows = None
                    break
            
            if all_rows is not None:
                all_rows.extend(rows)

        if all_rows is None:
            continue

        if not all_rows:
            print(f"Note: task {task_key}: no request rows found, skipping")
            continue

        task["feeder"] = {"sessions": session_ids, **feeder_agg.aggregate_rows(all_rows)}
        print(f"task {task_key}: wrote feeder block with {len(all_rows)} requests")

    from datetime import datetime
    state["feeder_enriched_at"] = datetime.utcnow().isoformat() + "Z"

    try:
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(args.state), prefix=".feeder_enrich_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
                f.write("\n")
            os.replace(tmp_path, args.state)
        except:
            os.unlink(tmp_path)
            raise
    except OSError as e:
        print(f"Error: cannot write state JSON: {e}")
        return 1

    if args.rerender:
        try:
            import sys
            from pathlib import Path
            sys.path.append(str(Path(__file__).resolve().parent.parent))
            import ringer
            artifacts_dir = Path(args.artifacts_dir).resolve()
            run_id = state.get("run_id")
            run_name = state.get("run_name")
            if not run_id or not run_name:
                print("Error: state missing run_id or run_name")
                return 4

            renderer = ringer.ArtifactRenderer(artifacts_dir / "x.html")
            pages = [
                artifacts_dir / f"{run_id}-report.html",
                artifacts_dir / "live" / f"{run_name}.html",
                artifacts_dir / f"{run_id}.html",
            ]
            for page_path in pages:
                if page_path.exists():
                    if page_path.name == f"{run_id}.html":
                        html = ringer.render_status_html(state, renderer, page_path=page_path)
                    else:
                        html = renderer.render_final_report_html(state, page_path=page_path)
                    page_path.write_text(html, encoding="utf-8")
                    print(f"Re-rendered: {page_path}")
                else:
                    print(f"Skipped (not found): {page_path}")
        except Exception as e:
            print(f"Error: rerender failed: {e}")
            return 4

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
