#!/usr/bin/env python3
"""
Ringer → Paperclip auto-projection hook.

After a Ringer run completes, this script reads the run state JSON and
posts the verdict as a comment to the linked Paperclip issue (if the manifest
carries a `paperclip_issue` field) and/or as a Beads comment (if `bead_id`
is present).

Usage:
    python3 paperclip_projector.py <run_state_json_path> [manifest_json_path]

The manifest path is optional; if omitted, the script will try to find it
by looking for a manifest.json in the parent directory of the run state.

Environment:
    PAPERCLIP_URL  — base URL (default: http://127.0.0.1:3100)
    BEADS_BIN      — path to bd CLI (default: bd from PATH)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone


def post_paperclip_comment(issue_id: str, body: str, base_url: str) -> dict:
    """Post a comment to a Paperclip issue. Returns the API response."""
    url = f"{base_url}/api/issues/{issue_id}/comments"
    data = json.dumps({"body": body}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode("utf-8", "replace")}
    except Exception as e:
        return {"error": str(e)}


def post_beads_comment(bead_id: str, comment: str, bd_bin: str) -> str:
    """Post a comment to a Beads issue. Returns the CLI output."""
    try:
        result = subprocess.run(
            [bd_bin, "comment", bead_id, comment],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout.strip() or result.stderr.strip() or "(no output)"
    except Exception as e:
        return f"error: {e}"


def format_verdict_comment(run_state: dict, manifest_data: dict | None) -> str:
    """Format the run verdict as a markdown comment for Paperclip."""
    run_id = run_state.get("run_id", "unknown")
    run_name = run_state.get("run_name", "unknown")
    identity = run_state.get("identity", "unknown")
    started_at = run_state.get("started_at", "unknown")
    state = run_state.get("state", "unknown")

    tasks = run_state.get("tasks", [])
    pass_count = sum(1 for t in tasks if t.get("status") == "pass")
    fail_count = sum(1 for t in tasks if t.get("status") == "fail")
    total_tokens = run_state.get("tokens", 0)

    verdicts = []
    for task in tasks:
        key = task.get("key", "?")
        status = task.get("status", "?")
        verdict = task.get("verdict", "")
        attempts = task.get("attempts", 0)
        elapsed = task.get("elapsed_s", 0)
        model = task.get("model", "")
        marker = "✅" if status == "pass" else "❌" if status == "fail" else "⏳"
        verdicts.append(
            f"| {marker} | `{key}` | {status} | {verdict} | {attempts} | {elapsed:.1f}s | {model} |"
        )

    artifact_path = run_state.get("artifact_path", "")
    report_path = run_state.get("report_path", "")

    comment = f"""## Ringer Verdict: {state.upper()}

| Field | Value |
|-------|-------|
| Run ID | `{run_id}` |
| Run name | {run_name} |
| Identity | {identity} |
| Started | {started_at} |
| State | **{state}** |
| Pass/Fail | {pass_count} pass / {fail_count} fail |
| Total tokens | {total_tokens:,} |

### Task verdicts

| | Key | Status | Verdict | Attempts | Elapsed | Model |
|---|---|---|---|---|---|---|
{chr(10).join(verdicts)}

### Artifacts

- Report: `{report_path}`
- Dashboard: `http://127.0.0.1:8700`
- Artifact: `{artifact_path}`

---
*Auto-projected by `paperclip_projector.py` at {datetime.now(timezone.utc).isoformat()}*
"""
    return comment


def format_beads_comment(run_state: dict) -> str:
    """Format a shorter comment for Beads."""
    run_id = run_state.get("run_id", "unknown")
    run_name = run_state.get("run_name", "unknown")
    state = run_state.get("state", "unknown")
    pass_count = sum(1 for t in run_state.get("tasks", []) if t.get("status") == "pass")
    fail_count = sum(1 for t in run_state.get("tasks", []) if t.get("status") == "fail")
    tokens = run_state.get("tokens", 0)

    verdicts = []
    for task in run_state.get("tasks", []):
        key = task.get("key", "?")
        status = task.get("status", "?")
        marker = "✅" if status == "pass" else "❌" if status == "fail" else "⏳"
        verdicts.append(f"  {marker} {key}: {status}")

    return (
        f"Ringer verdict: {state.upper()} — run {run_id} ({run_name})\n"
        f"  {pass_count} pass / {fail_count} fail, {tokens:,} tokens\n"
        + "\n".join(verdicts)
    )


def find_manifest(run_state_path: Path) -> dict | None:
    """Try to find the manifest JSON near the run state."""
    # Check if the run state itself has manifest fields embedded
    state = json.loads(run_state_path.read_text())
    # Look for paperclip_issue/bead_id in task specs
    for task in state.get("tasks", []):
        if task.get("paperclip_issue") or task.get("bead_id"):
            return state
    return None


def extract_cross_links(run_state: dict, manifest_data: dict | None) -> list[tuple[str, str]]:
    """Extract (paperclip_issue, bead_id) pairs from run state or manifest."""
    links = []
    # Check run state tasks first
    for task in run_state.get("tasks", []):
        pc = task.get("paperclip_issue")
        bd = task.get("bead_id")
        if pc or bd:
            links.append((pc or "", bd or ""))
    # Check manifest tasks if present
    if manifest_data and not links:
        for task in manifest_data.get("tasks", []):
            pc = task.get("paperclip_issue")
            bd = task.get("bead_id")
            if pc or bd:
                links.append((pc or "", bd or ""))
    # Deduplicate
    seen = set()
    unique = []
    for pc, bd in links:
        key = (pc, bd)
        if key not in seen:
            seen.add(key)
            unique.append((pc, bd))
    return unique


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: paperclip_projector.py <run_state_json_path> [manifest_json_path]", file=sys.stderr)
        return 1

    run_state_path = Path(sys.argv[1]).expanduser().resolve()
    if not run_state_path.is_file():
        print(f"Run state JSON not found: {run_state_path}", file=sys.stderr)
        return 1

    run_state = json.loads(run_state_path.read_text(encoding="utf-8"))

    manifest_data = None
    if len(sys.argv) >= 3:
        manifest_path = Path(sys.argv[2]).expanduser().resolve()
        if manifest_path.is_file():
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest_data = find_manifest(run_state_path)

    cross_links = extract_cross_links(run_state, manifest_data)
    if not cross_links:
        print("No paperclip_issue or bead_id found in run state or manifest. Nothing to project.")
        return 0

    base_url = os.environ.get("PAPERCLIP_URL", "http://127.0.0.1:3100")
    bd_bin = os.environ.get("BEADS_BIN", "bd")

    results = []

    for pc_issue, bead_id in cross_links:
        if pc_issue:
            comment = format_verdict_comment(run_state, manifest_data)
            result = post_paperclip_comment(pc_issue, comment, base_url)
            status = "ok" if "error" not in result else f"error: {result['error']}"
            results.append(f"Paperclip {pc_issue}: {status}")
            print(f"Paperclip {pc_issue}: {status}")

        if bead_id:
            comment = format_beads_comment(run_state)
            bd_result = post_beads_comment(bead_id, comment, bd_bin)
            results.append(f"Beads {bead_id}: {bd_result}")
            print(f"Beads {bead_id}: {bd_result}")

    # Log projection outcome
    log_dir = Path.home() / ".ringer" / "hooks"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "projection_log.jsonl"
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_state.get("run_id"),
        "run_name": run_state.get("run_name"),
        "state": run_state.get("state"),
        "cross_links": [{"paperclip_issue": pc, "bead_id": bd} for pc, bd in cross_links],
        "results": results,
    }
    with log_path.open("a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())