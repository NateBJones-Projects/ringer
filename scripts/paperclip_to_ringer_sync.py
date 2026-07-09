#!/usr/bin/env python3
"""
Paperclip -> Ringer fleet sync.

Reads the live Paperclip coordination surface (company / projects / issues on
:3100) and emits two Ringer artifacts:

  1. registry/paperclip-fleet.json   -- the STRUCTURE (directory): company,
     projects, per-project + per-status issue counts, and the actionable
     open work list. This is what a Ringer swarm reads to know the fleet shape.

  2. manifests/paperclip-fleet-sync.json -- the CONTENT as a runnable Ringer
     manifest: one task per actionable Paperclip issue, each carrying a
     check-gate (exit 0 = pass) per the adopt-Ringer decision (hermes-6ax):
     "Ringer is a check-gate layer over Paperclip, not a new harness."

Faithful to fleet doctrine: Paperclip issues/beads ARE the manifest source.
Idempotent: re-run to refresh. Reversible: only writes two files in the repo.

Usage:  python3 scripts/paperclip_to_ringer_sync.py [--dry-run]
"""
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

PAPERCLIP = os.environ.get("PAPERCLIP_URL", "http://127.0.0.1:3100")
COMPANY_ID = os.environ.get("PAPERCLIP_COMPANY_ID", "87c32b8e-f131-4df8-ad8e-963d01b458e7")
COMPANY_NAME = "jack.digital"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Statuses that represent live, actionable work (become Ringer tasks).
ACTIONABLE = {"todo", "in_progress", "in_review", "blocked"}
# Cap manifest task count so a sync stays runnable; log what is dropped.
MAX_TASKS = int(os.environ.get("PAPERCLIP_SYNC_MAX_TASKS", "24"))


def _get(path):
    url = f"{PAPERCLIP}{path}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())


def _issues():
    """Fetch issues, paging until exhausted (API caps a page at ~500)."""
    seen, out, offset = set(), [], 0
    while True:
        try:
            page = _get(f"/api/companies/{COMPANY_ID}/issues?limit=500&offset={offset}")
        except Exception as e:
            print(f"WARN: issue fetch failed at offset {offset}: {e}", file=sys.stderr)
            break
        rows = page if isinstance(page, list) else page.get("issues", page.get("data", []))
        if not rows:
            break
        new = [i for i in rows if i.get("id") not in seen]
        for i in new:
            seen.add(i.get("id"))
        out.extend(new)
        if len(rows) < 500 or not new:
            break
        offset += 500
    return out


def _projects():
    try:
        return _get(f"/api/companies/{COMPANY_ID}/projects")
    except Exception as e:
        print(f"WARN: project fetch failed: {e}", file=sys.stderr)
        return []


def build():
    now = datetime.now(timezone.utc).isoformat()
    projects = _projects()
    issues = _issues()
    proj_by_id = {p["id"]: p for p in projects}

    # ---- structure: per-project + per-status rollup ----
    status_by_project = defaultdict(Counter)
    for i in issues:
        status_by_project[i.get("projectId")][i.get("status", "unknown")] += 1

    project_dir = []
    for p in projects:
        counts = dict(status_by_project.get(p["id"], {}))
        project_dir.append({
            "id": p["id"],
            "name": p.get("name"),
            "urlKey": p.get("urlKey"),
            "status": p.get("status"),
            "repoUrl": (p.get("codebase") or {}).get("repoUrl"),
            "issue_counts": counts,
            "actionable": sum(counts.get(s, 0) for s in ACTIONABLE),
        })
    # issues with no project (company-level)
    no_proj = dict(status_by_project.get(None, {}))
    if no_proj:
        project_dir.append({
            "id": None, "name": "(company-level / unassigned)", "urlKey": None,
            "status": None, "repoUrl": None, "issue_counts": no_proj,
            "actionable": sum(no_proj.get(s, 0) for s in ACTIONABLE),
        })

    actionable = [i for i in issues if i.get("status") in ACTIONABLE]
    # rank: priority (high>medium>low), then blocked/in_progress first
    prio_rank = {"high": 0, "medium": 1, "low": 2, None: 3}
    status_rank = {"in_progress": 0, "in_review": 1, "todo": 2, "blocked": 3}
    actionable.sort(key=lambda i: (prio_rank.get(i.get("priority"), 3),
                                   status_rank.get(i.get("status"), 4),
                                   i.get("identifier", "")))

    fleet = {
        "synced_at": now,
        "source": {"surface": "paperclip", "url": PAPERCLIP,
                   "company_id": COMPANY_ID, "company": COMPANY_NAME},
        "doctrine": "Paperclip issues are the Ringer manifest source; Ringer is a "
                    "check-gate layer over Paperclip (decision hermes-6ax).",
        "totals": {
            "issues": len(issues),
            "by_status": dict(Counter(i.get("status", "unknown") for i in issues)),
            "actionable": len(actionable),
            "projects": len(projects),
        },
        "projects": sorted(project_dir, key=lambda x: -x["actionable"]),
        "actionable_issues": [
            {
                "identifier": i.get("identifier"),
                "title": i.get("title"),
                "status": i.get("status"),
                "priority": i.get("priority"),
                "project": (proj_by_id.get(i.get("projectId")) or {}).get("name"),
            }
            for i in actionable
        ],
    }
    return fleet, actionable, proj_by_id, now


def make_manifest(actionable, proj_by_id, now):
    tasks = []
    for i in actionable[:MAX_TASKS]:
        ident = i.get("identifier", "UNKNOWN")
        title = (i.get("title") or "").replace("\n", " ").strip()
        proj = (proj_by_id.get(i.get("projectId")) or {}).get("name", "unassigned")
        # Check gate per hermes-6ax: worker must leave durable evidence
        # (a receipt file) — exit 0 = pass. Deliberately generic; per-issue
        # gates are authored from the hermes-6ee constitutions later.
        receipt = f"receipt_{ident}.md"
        tasks.append({
            "key": ident.lower().replace("-", "_"),
            "task_type": "fleet-work",
            "engine": "opencode",
            "model": "openrouter/z-ai/glm-5.2",
            "timeout_s": 1800,
            "expect_files": [receipt],
            "spec": (
                f"You are a fleet worker executing Paperclip issue {ident} "
                f"(project: {proj}, priority: {i.get('priority')}). "
                f"TITLE: {title}\n\n"
                f"Do the work in your current task directory only; never write "
                f"outside it. When done, write ./{receipt} — a durable evidence "
                f"receipt with: (1) what you did, (2) exact commands/paths of "
                f"artifacts produced, (3) how it was verified, (4) any caveats. "
                f"'Done' = artifact + path + verification (fleet durable-evidence "
                f"contract). Do not claim completion without the receipt."
            ),
            "check": (
                f"test -s {receipt} && "
                f"grep -qiE 'verif|verified|check|test' {receipt} && "
                f"echo '{ident} receipt-gate OK'"
            ),
            "verified": f"{receipt} exists, is non-empty, and records a verification step",
            "paperclip_ref": ident,
        })
    return {
        "run_name": "paperclip-fleet-sync",
        "_synced_at": now,
        "_source": "generated by scripts/paperclip_to_ringer_sync.py from Paperclip :3100",
        "_doctrine": "check-gate layer over Paperclip (hermes-6ax); receipt-gate = durable-evidence contract",
        "workdir": "/tmp/ringer-paperclip-fleet-sync",
        "max_parallel": 3,
        "tasks": tasks,
    }


def main():
    dry = "--dry-run" in sys.argv
    fleet, actionable, proj_by_id, now = build()
    manifest = make_manifest(actionable, proj_by_id, now)

    dropped = max(0, len(actionable) - MAX_TASKS)
    reg_path = os.path.join(REPO, "registry", "paperclip-fleet.json")
    man_path = os.path.join(REPO, "manifests", "paperclip-fleet-sync.json")

    print(f"Paperclip -> Ringer sync @ {now}")
    print(f"  company={COMPANY_NAME}  projects={fleet['totals']['projects']}  "
          f"issues={fleet['totals']['issues']}  actionable={fleet['totals']['actionable']}")
    print(f"  by_status={fleet['totals']['by_status']}")
    print(f"  manifest tasks={len(manifest['tasks'])}"
          + (f"  (DROPPED {dropped} beyond MAX_TASKS={MAX_TASKS} — raise "
             f"PAPERCLIP_SYNC_MAX_TASKS to include)" if dropped else ""))

    if dry:
        print("  [dry-run] no files written")
        return
    os.makedirs(os.path.dirname(reg_path), exist_ok=True)
    os.makedirs(os.path.dirname(man_path), exist_ok=True)
    with open(reg_path, "w") as f:
        json.dump(fleet, f, indent=2)
    with open(man_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"  wrote {os.path.relpath(reg_path, REPO)}")
    print(f"  wrote {os.path.relpath(man_path, REPO)}")


if __name__ == "__main__":
    main()
