#!/usr/bin/env python3
"""Read-only verifier for fleet launch receipts (schema/launch-receipt.v1.json).

Checks the append-only receipts JSONL and, optionally, joins Claude Code
session transcripts against it. Exits non-zero on violations. It never writes
anything anywhere — abandoned candidates are reported, not stamped.

Violations (exit 1):
  - unparseable or schema-invalid receipt lines
  - forbidden secret material anywhere in a receipt line
  - lifecycle order problems (a receipt_id whose first event is not 'launched')
  - unattributed launches: post-cutover sdk-cli/external sessions with no receipt

Warnings (reported, exit 0):
  - unbound receipts (claude-code-session launched but session_id never bound)
  - stale open receipts (no terminal event after --stale-hours; abandoned candidates)

Usage:
  tools/verify_launch_receipts.py [--receipts PATH] [--projects DIR | --no-projects]
                                  [--stale-hours N] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_RECEIPTS = Path.home() / ".ringer" / "receipts" / "launches.jsonl"
DEFAULT_PROJECTS = Path.home() / ".claude" / "projects"

RECEIPT_EVENTS = ("launched", "bound", "completed", "failed", "abandoned")
TERMINAL_EVENTS = ("completed", "failed", "abandoned")
LAUNCHER_KINDS = ("human", "agent", "orchestrator", "scheduler")
LAUNCHED_KINDS = ("claude-code-session", "worker", "command")
PERMISSION_MODES = ("default", "plan", "bypassPermissions", "full_access", "sandbox")
RECEIPT_ID_PATTERN = re.compile(r"^lr-[0-9A-HJKMNP-TV-Z]{26}$")
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GRANT_PATTERN = re.compile(r"^[A-Za-z0-9*:_.-]+$")

# Mirrors RECEIPT_FORBIDDEN_PATTERNS in ringer.py — the writer rejects these
# shapes; the verifier proves nothing slipped through another path.
FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("url-with-query-string", re.compile(r"https?://[^\s\"'\\]*\?[^\s\"'\\]+", re.IGNORECASE)),
    ("authorization-header", re.compile(r"\bauthorization\b\s*[:=]", re.IGNORECASE)),
    ("bearer-token", re.compile(r"\bbearer\s+[A-Za-z0-9._~+/-]{8,}=*", re.IGNORECASE)),
    (
        "oauth-or-key-param",
        re.compile(
            r"\b(code|state|token|access_token|refresh_token|id_token|client_secret"
            r"|code_verifier|code_challenge|api[_-]?key|secret)\s*=",
            re.IGNORECASE,
        ),
    ),
    ("jwt-shaped", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("cookie-header", re.compile(r"\b(set-)?cookie\b\s*[:=]", re.IGNORECASE)),
)


def scan_forbidden(text: str) -> list[str]:
    return [name for name, pattern in FORBIDDEN_PATTERNS if pattern.search(text)]


def _check_optional_str(value: Any, max_len: int, field: str, errors: list[str]) -> None:
    if value is not None and (not isinstance(value, str) or len(value) > max_len):
        errors.append(f"{field} must be a string of <= {max_len} chars or null")


def validate_receipt(obj: Any) -> list[str]:
    """Structural validation mirroring schema/launch-receipt.v1.json. Returns errors."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["receipt must be a JSON object"]
    known = {
        "receipt_version", "receipt_id", "event", "emitted_at", "host",
        "launcher", "launched", "work", "side_effect_grants", "notes",
    }
    for key in obj:
        if key not in known:
            errors.append(f"unknown field: {key}")
    if obj.get("receipt_version") != 1:
        errors.append("receipt_version must be 1")
    if not isinstance(obj.get("receipt_id"), str) or not RECEIPT_ID_PATTERN.match(obj["receipt_id"]):
        errors.append("receipt_id must match ^lr-[Crockford ULID x26]$")
    event = obj.get("event")
    if event not in RECEIPT_EVENTS:
        errors.append(f"event must be one of {RECEIPT_EVENTS}")
    emitted = obj.get("emitted_at")
    if not isinstance(emitted, str) or not TIMESTAMP_PATTERN.match(emitted):
        errors.append("emitted_at must be an ISO 8601 timestamp")
    host = obj.get("host")
    if not isinstance(host, str) or not (1 <= len(host) <= 64):
        errors.append("host must be a 1..64 char string")

    launcher = obj.get("launcher")
    if not isinstance(launcher, dict):
        errors.append("launcher must be an object")
    else:
        for key in launcher:
            if key not in {"identity", "kind", "pid", "run_id", "session_id"}:
                errors.append(f"launcher.{key} is not a known field")
        identity = launcher.get("identity")
        if not isinstance(identity, str) or not (1 <= len(identity) <= 120):
            errors.append("launcher.identity must be a 1..120 char string")
        if launcher.get("kind") not in LAUNCHER_KINDS:
            errors.append(f"launcher.kind must be one of {LAUNCHER_KINDS}")
        pid = launcher.get("pid")
        if pid is not None and (not isinstance(pid, int) or isinstance(pid, bool) or pid < 1):
            errors.append("launcher.pid must be a positive integer or null")
        _check_optional_str(launcher.get("run_id"), 200, "launcher.run_id", errors)
        _check_optional_str(launcher.get("session_id"), 200, "launcher.session_id", errors)

    launched = obj.get("launched")
    if event == "launched" and launched is None:
        errors.append("a 'launched' receipt requires the launched section")
    if launched is not None:
        if not isinstance(launched, dict):
            errors.append("launched must be an object")
        else:
            for key in launched:
                if key not in {
                    "kind", "entrypoint", "session_id", "model", "cwd",
                    "permission_mode", "prompt_sha256",
                }:
                    errors.append(f"launched.{key} is not a known field")
            if launched.get("kind") not in LAUNCHED_KINDS:
                errors.append(f"launched.kind must be one of {LAUNCHED_KINDS}")
            entrypoint = launched.get("entrypoint")
            if not isinstance(entrypoint, str) or not (1 <= len(entrypoint) <= 120):
                errors.append("launched.entrypoint must be a 1..120 char string")
            cwd = launched.get("cwd")
            if not isinstance(cwd, str) or not (1 <= len(cwd) <= 1024):
                errors.append("launched.cwd must be a 1..1024 char string")
            _check_optional_str(launched.get("session_id"), 200, "launched.session_id", errors)
            _check_optional_str(launched.get("model"), 200, "launched.model", errors)
            mode = launched.get("permission_mode")
            if mode is not None and mode not in PERMISSION_MODES:
                errors.append(f"launched.permission_mode must be one of {PERMISSION_MODES} or null")
            digest = launched.get("prompt_sha256")
            if digest is not None and (not isinstance(digest, str) or not SHA256_PATTERN.match(digest)):
                errors.append("launched.prompt_sha256 must be a 64-char lowercase hex digest or null")

    work = obj.get("work")
    if event == "launched" and work is None:
        errors.append("a 'launched' receipt requires the work section")
    if work is not None:
        if not isinstance(work, dict):
            errors.append("work must be an object")
        else:
            for key in work:
                if key not in {"bead_id", "intent"}:
                    errors.append(f"work.{key} is not a known field")
            intent = work.get("intent")
            if not isinstance(intent, str) or not (1 <= len(intent) <= 140):
                errors.append("work.intent must be a 1..140 char string")
            _check_optional_str(work.get("bead_id"), 120, "work.bead_id", errors)

    grants = obj.get("side_effect_grants")
    if grants is not None:
        if not isinstance(grants, list) or len(grants) > 32:
            errors.append("side_effect_grants must be a list of <= 32 items")
        else:
            for item in grants:
                if not isinstance(item, str) or len(item) > 120 or not GRANT_PATTERN.match(item):
                    errors.append(f"side_effect_grants item is not a coarse capability class: {item!r}")
    notes = obj.get("notes")
    if notes is not None and (not isinstance(notes, str) or len(notes) > 180):
        errors.append("notes must be a string of <= 180 chars")
    return errors


def parse_timestamp(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def load_receipt_lines(path: Path) -> tuple[list[tuple[int, dict]], list[str]]:
    """Returns ([(lineno, receipt)], violations). Read-only."""
    records: list[tuple[int, dict]] = []
    violations: list[str] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return records, violations
    for lineno, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        for name in scan_forbidden(line):
            violations.append(f"{path.name}:{lineno}: forbidden material ({name})")
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            violations.append(f"{path.name}:{lineno}: unparseable JSON ({exc.msg})")
            continue
        # Scan decoded string values too. Raw-line scanning alone can miss a
        # forbidden shape represented with JSON unicode escapes.
        pending = [obj]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                for name in scan_forbidden(value):
                    marker = f"{path.name}:{lineno}: forbidden material ({name})"
                    if marker not in violations:
                        violations.append(marker)
            elif isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        for error in validate_receipt(obj):
            violations.append(f"{path.name}:{lineno}: schema violation: {error}")
        if isinstance(obj, dict):
            records.append((lineno, obj))
    return records, violations


def resolve_latest(records: list[tuple[int, dict]]) -> dict[str, dict]:
    """Latest line per receipt_id wins; the first line's launched/work sections carry forward."""
    resolved: dict[str, dict] = {}
    for _, obj in records:
        receipt_id = obj.get("receipt_id")
        if not isinstance(receipt_id, str):
            continue
        merged = dict(resolved.get(receipt_id, {}))
        first_seen = not merged
        for key, value in obj.items():
            if value is not None or key not in merged:
                merged[key] = value
        merged["_first_event"] = obj.get("event") if first_seen else merged.get("_first_event")
        resolved[receipt_id] = merged
    return resolved


def scan_sessions(projects_dir: Path, cutover: datetime) -> list[dict]:
    """Find sdk-cli / external session transcripts started at or after cutover. Read-only."""
    sessions: list[dict] = []
    if not projects_dir.is_dir():
        return sessions
    for transcript in sorted(projects_dir.glob("*/*.jsonl")):
        session_id = None
        user_type = None
        entrypoint = None
        started = None
        try:
            with transcript.open("r", encoding="utf-8", errors="replace") as fh:
                for _ in range(25):
                    line = fh.readline()
                    if not line:
                        break
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    session_id = session_id or entry.get("sessionId")
                    user_type = user_type or entry.get("userType")
                    entrypoint = entrypoint or entry.get("entrypoint")
                    if started is None and isinstance(entry.get("timestamp"), str):
                        started = parse_timestamp(entry["timestamp"])
                    if session_id and user_type and started:
                        break
        except OSError:
            continue
        if not session_id or started is None or started < cutover:
            continue
        if user_type != "external" and entrypoint != "sdk-cli":
            continue
        sessions.append(
            {
                "session_id": session_id,
                "path": str(transcript),
                "started": started.isoformat(),
                "entrypoint": entrypoint,
                "user_type": user_type,
            }
        )
    return sessions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS)
    parser.add_argument(
        "--projects",
        type=Path,
        default=None,
        help=(
            "optionally join Claude transcripts under this directory; opt-in until "
            "launchers emit bound session events"
        ),
    )
    parser.add_argument("--no-projects", action="store_true", help="skip the session-transcript join")
    parser.add_argument("--stale-hours", type=float, default=24.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    records, violations = load_receipt_lines(args.receipts)
    resolved = resolve_latest(records)
    warnings: list[str] = []
    now = datetime.now(timezone.utc)

    bound_session_ids: set[str] = set()
    for receipt_id, receipt in sorted(resolved.items()):
        if receipt.get("_first_event") != "launched":
            violations.append(
                f"{receipt_id}: first event is {receipt.get('_first_event')!r}, expected 'launched'"
            )
        launched = receipt.get("launched") or {}
        session_id = launched.get("session_id") if isinstance(launched, dict) else None
        if isinstance(session_id, str):
            bound_session_ids.add(session_id)
        latest_event = receipt.get("event")
        if latest_event in TERMINAL_EVENTS:
            continue
        if isinstance(launched, dict) and launched.get("kind") == "claude-code-session" and not session_id:
            warnings.append(f"{receipt_id}: unbound (claude-code-session with no bound session_id)")
        emitted = parse_timestamp(str(receipt.get("emitted_at", "")))
        if emitted is not None and now - emitted > timedelta(hours=args.stale_hours):
            warnings.append(
                f"{receipt_id}: stale open receipt (latest event {latest_event!r} at "
                f"{receipt.get('emitted_at')}; abandoned candidate after {args.stale_hours:g}h)"
            )

    unattributed: list[dict] = []
    if not args.no_projects and args.projects is not None and records:
        launch_times = [
            parse_timestamp(str(obj.get("emitted_at", ""))) for _, obj in records
        ]
        cutover = min((t for t in launch_times if t is not None), default=None)
        if cutover is not None:
            for session in scan_sessions(args.projects, cutover):
                if session["session_id"] not in bound_session_ids:
                    unattributed.append(session)
                    violations.append(
                        f"unattributed launch: session {session['session_id']} "
                        f"({session['path']}) started {session['started']} with no receipt"
                    )

    report = {
        "receipts_path": str(args.receipts),
        "receipt_lines": len(records),
        "receipt_ids": len(resolved),
        "violations": violations,
        "warnings": warnings,
        "unattributed_launches": unattributed,
        "ok": not violations,
    }
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"receipts: {report['receipt_lines']} lines, {report['receipt_ids']} receipt ids "
              f"({report['receipts_path']})")
        for item in violations:
            print(f"VIOLATION: {item}")
        for item in warnings:
            print(f"warning: {item}")
        print("OK" if report["ok"] else f"FAIL: {len(violations)} violation(s)")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
