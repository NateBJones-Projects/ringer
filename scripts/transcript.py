"""Parse a raw Ringer worker.log into a readable, turn-by-turn transcript.

The transcript is a VIEW over the unchanged raw log (Ringer invariant: "logs carry
raw worker output only") — nothing here mutates the log; it only reconstructs the
conversation between the orchestrator (who sent the spec) and the worker (an OpenCode
agent) for the live agent-wall UI.

A worker.log is one or more ATTEMPTS concatenated. Each attempt is:
    [ringer.py] attempt N started <iso>
    [ringer.py] engine: <opencode|mock|...>
    [ringer.py] command: <shell cmdline ...>     # may span many physical lines;
                                                   # its last arg is the single-quoted
                                                   # spec prompt, and the whole command
                                                   # ALWAYS ends in `< /dev/null`.
    <NDJSON event lines>                           # the real worker output
    [ringer.py] attempt N exited rc=<int>

THE RETRY TRAP: on attempt 2+, the spec arg has, appended after the base spec,
`Previous attempt failed: ` followed by a FULL REPRODUCTION of the previous attempt's
log — including reproduced `[ringer.py] ...` lines AND reproduced `{"type":...}` event
lines, all still INSIDE the quoted arg (with single quotes shell-escaped as '"'"').
We must never treat that embedded reproduction as real markers/events. We fence it off
using the stdin-close invariant: the real command ends at the LAST line ending in
`< /dev/null`; everything after that line is this attempt's real event stream.

Stdlib only.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

SCHEMA = 1

_ATTEMPT_START = re.compile(r"^\[ringer\.py\] attempt (\d+) started (.*)$", re.M)
_ENGINE_LINE = re.compile(r"^\[ringer\.py\] engine:\s*(\S+)", re.M)
_EXITED = re.compile(r"^\[ringer\.py\] attempt \d+ exited rc=(-?\d+)", re.M)
_DEVNULL = re.compile(r"<\s*/dev/null\s*$")
_MAX_FIELD = 4000  # truncate large tool input/output blobs for the transcript payload


def parse_transcript(log_text: str, *, task: Optional[dict] = None,
                     max_bytes: int = 2_000_000) -> dict:
    """Parse worker-log text into the canonical transcript dict. Never raises on
    malformed/truncated input — problems are collected into parse_warnings."""
    warnings: list[str] = []
    if len(log_text) > max_bytes:
        log_text = log_text[-max_bytes:]
        warnings.append(f"log truncated to last {max_bytes} bytes; leading attempts may be partial")

    engine = _engine(log_text)
    segments = _segment_attempts(log_text)
    if not segments and log_text.strip():
        warnings.append("no attempt markers found; treating whole log as one attempt")
        segments = [(1, "", log_text)]

    attempts = [_parse_attempt(n, started_at, seg, engine, task, warnings)
                for (n, started_at, seg) in segments]

    sessions: list[str] = []
    for a in attempts:
        sid = a.get("session_id")
        if sid and sid not in sessions:
            sessions.append(sid)

    status = attempts[-1]["outcome"] if attempts else "failed"
    tokens_total = 0
    if attempts and attempts[-1].get("tokens_final"):
        tokens_total = attempts[-1]["tokens_final"].get("total", 0)

    return {
        "schema": SCHEMA,
        "engine": engine,
        "sessions": sessions,
        "status": status,
        "attempts": attempts,
        "tokens_total": tokens_total,
        "parse_warnings": warnings,
    }


def parse_transcript_file(path, *, task: Optional[dict] = None,
                          max_bytes: int = 2_000_000) -> dict:
    """Read a worker.log (tail to max_bytes if larger) and parse it."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        return {"schema": SCHEMA, "engine": "unknown", "sessions": [], "status": "failed",
                "attempts": [], "tokens_total": 0, "parse_warnings": [f"cannot read log: {exc}"]}
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    return parse_transcript(data.decode("utf-8", errors="replace"), task=task, max_bytes=max_bytes)


def _engine(log_text: str) -> str:
    m = _ENGINE_LINE.search(log_text)
    return m.group(1) if m else "unknown"


def _segment_attempts(log_text: str) -> list[tuple[int, str, str]]:
    """Split into (n, started_at, segment_text) tuples, one per REAL attempt.
    Only start-of-line `attempt N started` markers open a segment, so the embedded
    reproduction (prefixed with 'Previous attempt failed: ') never triggers a split."""
    marks = list(_ATTEMPT_START.finditer(log_text))
    if not marks:
        return []
    segments = []
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(log_text)
        segments.append((int(m.group(1)), m.group(2).strip(), log_text[start:end]))
    return segments


def _events_region(segment: str) -> str:
    """Return the real NDJSON/plain-text region: everything after the LAST physical
    line ending in `< /dev/null` (the real command's stdin-close). This fences off the
    command preamble AND any embedded prior-attempt reproduction it contains."""
    lines = segment.splitlines()
    last_cmd_end = -1
    for i, line in enumerate(lines):
        if _DEVNULL.search(line):
            last_cmd_end = i
    if last_cmd_end == -1:
        return ""
    return "\n".join(lines[last_cmd_end + 1:])


def _spec_prompt(segment: str, events_region: str) -> str:
    """Best-effort recovery of the command's single-quoted spec argument."""
    idx = segment.find("[ringer.py] command:")
    if idx == -1:
        return ""
    # the preamble is everything from command: up to where real events begin
    preamble = segment[idx:]
    if events_region:
        cut = preamble.rfind(events_region)
        if cut != -1:
            preamble = preamble[:cut]
    cmd = preamble[len("[ringer.py] command:"):].strip()
    # strip the trailing stdin-close
    cmd = re.sub(r"<\s*/dev/null\s*$", "", cmd.strip()).strip()
    first = cmd.find("'")
    last = cmd.rfind("'")
    if first == -1 or last <= first:
        return cmd
    arg = cmd[first + 1:last]
    # unescape shell single-quote runs: '"'"'  ->  '
    return arg.replace("'\"'\"'", "'")


def _map_event(event: dict) -> Optional[dict]:
    et = event.get("type")
    part = event.get("part") or {}
    sid = event.get("sessionID")
    if et == "text":
        t = part.get("time") or {}
        return {"role": "worker", "kind": "text", "text": part.get("text", ""),
                "session_id": sid, "t_start": t.get("start"), "t_end": t.get("end")}
    if et == "tool_use":
        state = part.get("state") or {}
        return {"role": "worker", "kind": "tool", "tool": part.get("tool"),
                "input": _truncate(state.get("input")), "output": _truncate(state.get("output")),
                "exit": (state.get("metadata") or {}).get("exit"),
                "ok": state.get("status") == "completed", "call_id": part.get("callID"),
                "session_id": sid,
                "t_start": (state.get("time") or {}).get("start"),
                "t_end": (state.get("time") or {}).get("end")}
    if et == "step_finish":
        return {"role": "worker", "kind": "step", "reason": part.get("reason"),
                "tokens": part.get("tokens"), "cost": part.get("cost", 0), "session_id": sid}
    if et == "error":
        err = event.get("error") or {}
        return {"role": "worker", "kind": "error",
                "message": (err.get("data") or {}).get("message") or err.get("name"),
                "name": err.get("name"), "session_id": sid}
    return None  # step_start and anything else are structural-only


def _truncate(val: Any) -> Any:
    if isinstance(val, str) and len(val) > _MAX_FIELD:
        return val[:_MAX_FIELD] + "… [truncated]"
    return val


def _parse_attempt(n: int, started_at: str, segment: str, engine: str,
                   task: Optional[dict], warnings: list[str]) -> dict:
    events_region = _events_region(segment)
    spec_prompt = _spec_prompt(segment, events_region)

    turns: list[dict] = []
    tokens_final: Optional[dict] = None
    saw_error = False
    saw_stop = False

    if engine == "opencode":
        for line in events_region.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                warnings.append(f"attempt {n}: skipped a malformed/truncated event line")
                continue
            turn = _map_event(event)
            if not turn:
                continue
            turns.append(turn)
            if turn["kind"] == "step":
                if turn.get("tokens"):
                    tokens_final = turn["tokens"]
                if turn.get("reason") == "stop":
                    saw_stop = True
            elif turn["kind"] == "error":
                saw_error = True
    else:
        # non-JSON engine (e.g. mock): surface the plain-text body as one worker turn
        body = "\n".join(l for l in events_region.splitlines() if l.strip())
        if body:
            turns.append({"role": "worker", "kind": "text", "text": body,
                          "session_id": None, "t_start": None, "t_end": None})

    # orchestrator turn first: base spec on attempt 1, the injected reply on retries
    if n == 1:
        turns.insert(0, {"role": "orchestrator", "kind": "spec",
                         "text": (task or {}).get("spec") or spec_prompt})
    else:
        marker = "Previous attempt failed:"
        idx = spec_prompt.find(marker)
        reply = spec_prompt[idx:] if idx != -1 else spec_prompt
        turns.insert(0, {"role": "orchestrator", "kind": "retry_reply", "text": reply})

    rc = _rc(segment, events_region)
    if rc == 0 or (rc is None and saw_stop):
        outcome = "success"
    elif rc is None:
        outcome = "running"
    else:
        outcome = "failed"
    if saw_error and rc != 0:
        outcome = "failed"

    session_id = next((t["session_id"] for t in turns
                       if t.get("role") == "worker" and t.get("session_id")), None)

    return {"n": n, "started_at": started_at, "session_id": session_id, "rc": rc,
            "outcome": outcome, "turns": turns, "tokens_final": tokens_final, "cost": 0}


def _rc(segment: str, events_region: str) -> Optional[int]:
    """Exit code from the LAST real `exited rc=` marker (i.e. in the events region, so
    an embedded reproduction's fake `exited` line in the preamble is ignored)."""
    region = events_region if events_region else segment
    matches = _EXITED.findall(region)
    return int(matches[-1]) if matches else None
