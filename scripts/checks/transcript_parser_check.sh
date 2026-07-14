#!/bin/sh
# Check: scripts/transcript.py turns a raw worker.log into a readable, correctly
# segmented transcript. The acid test is the retry fixture, whose attempt-2 command
# preamble embeds a FULL reproduction of attempt-1's log (fake [ringer.py] markers +
# reproduced NDJSON events) — the parser must exclude every embedded line and only
# surface real events. Runs with cwd = task worktree (or repo root). POSIX/dash-safe.
set -u

test -f scripts/transcript.py || { echo 'FAIL: scripts/transcript.py does not exist'; exit 1; }
# Fixtures resolve worktree-relative first (normal runs), then the main-repo
# absolute path (so a worker building in an isolated git worktree — which lacks
# the untracked fixtures — still finds them).
FX=tests/fixtures/worker-logs
test -f "$FX/happy.opencode.log" || FX=/home/ajo/ringer/tests/fixtures/worker-logs
for f in happy.opencode.log retry-embedded.opencode.log mock.plainfile.log; do
  test -f "$FX/$f" || { echo "FAIL: missing fixture $FX/$f"; exit 1; }
done
export RINGER_FIXTURES="$FX"

python3 - <<'PY'
import sys, os, pathlib, importlib.util

FX = pathlib.Path(os.environ["RINGER_FIXTURES"])
spec = importlib.util.spec_from_file_location("transcript", "scripts/transcript.py")
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(f"FAIL: scripts/transcript.py did not import: {e!r}"); sys.exit(1)

problems = []
def check(cond, msg):
    if not cond: problems.append(msg)

def has_api():
    return hasattr(mod, "parse_transcript") and hasattr(mod, "parse_transcript_file")
if not has_api():
    print("FAIL: transcript.py must expose parse_transcript(log_text,*,task=None,max_bytes=...) "
          "and parse_transcript_file(path,*,task=None,max_bytes=...)"); sys.exit(1)

def parse(name):
    return mod.parse_transcript_file(str(FX / name))

def worker_turns(att, kind=None):
    return [t for t in att["turns"] if t.get("role") == "worker" and (kind is None or t.get("kind") == kind)]
def orch_turns(att, kind=None):
    return [t for t in att["turns"] if t.get("role") == "orchestrator" and (kind is None or t.get("kind") == kind)]

# ---------- 1. HAPPY (single attempt, success) ----------
try:
    d = parse("happy.opencode.log")
    check(d.get("engine") == "opencode", f"happy: engine should be opencode, got {d.get('engine')!r}")
    check(d.get("status") == "success", f"happy: status should be success, got {d.get('status')!r}")
    check(len(d.get("attempts", [])) == 1, f"happy: expected 1 attempt, got {len(d.get('attempts',[]))}")
    a = d["attempts"][0]
    check(a.get("outcome") == "success", f"happy: attempt outcome should be success, got {a.get('outcome')!r}")
    check(a.get("rc") == 0, f"happy: rc should be 0, got {a.get('rc')!r}")
    spec_turns = orch_turns(a, "spec")
    check(len(spec_turns) >= 1 and a["turns"][0].get("role") == "orchestrator",
          "happy: first turn must be the orchestrator's spec")
    check(any("Create alpha.txt" in (t.get("text") or "") for t in spec_turns),
          "happy: spec turn must contain the real brief text 'Create alpha.txt'")
    tools = worker_turns(a, "tool")
    check(any(t.get("tool") == "write" and "Wrote file successfully." in str(t.get("output",""))
              for t in tools), "happy: must surface the write tool call with its output")
    check(any(t.get("input") is not None and t.get("output") is not None for t in tools),
          "happy: tool turns must carry BOTH input and output")
    texts = worker_turns(a, "text")
    check(any((t.get("text") or "").strip() == "Done." for t in texts),
          "happy: final worker text 'Done.' must be present")
    check(d.get("tokens_total") == 7314, f"happy: tokens_total should be 7314 (last step_finish total), got {d.get('tokens_total')!r}")
except Exception as e:
    problems.append(f"happy: parser raised {e!r}")

# ---------- 2. RETRY-EMBEDDED (2 attempts; acid test for embedded exclusion) ----------
try:
    d = parse("retry-embedded.opencode.log")
    check(len(d.get("attempts", [])) == 2, f"retry: expected 2 attempts, got {len(d.get('attempts',[]))}")
    check(d.get("status") == "success", f"retry: overall status should be success (final attempt), got {d.get('status')!r}")
    a1, a2 = d["attempts"][0], d["attempts"][1]
    check(a1.get("outcome") == "failed", f"retry: attempt 1 outcome should be failed, got {a1.get('outcome')!r}")
    check(a1.get("rc") == 1, f"retry: attempt 1 rc should be 1, got {a1.get('rc')!r}")
    check(a2.get("outcome") == "success", f"retry: attempt 2 outcome should be success, got {a2.get('outcome')!r}")
    check(a2.get("rc") == 0, f"retry: attempt 2 rc should be 0, got {a2.get('rc')!r}")
    check((a1.get("session_id") or "").endswith("8GsYswD5"),
          f"retry: attempt 1 session should end 8GsYswD5, got {a1.get('session_id')!r}")
    check((a2.get("session_id") or "").endswith("lNLOee95"),
          f"retry: attempt 2 session should end lNLOee95, got {a2.get('session_id')!r}")
    # attempt 1 must end in an error turn
    check(any(t.get("kind") == "error" for t in worker_turns(a1)),
          "retry: attempt 1 must surface the provider error turn")
    # THE ACID TEST: every worker turn in attempt 2 belongs to attempt-2's session,
    # never the embedded attempt-1 reproduction (…8GsYswD5).
    a2_sessions = {t.get("session_id") for t in worker_turns(a2) if t.get("session_id")}
    check(a2_sessions and all(s.endswith("lNLOee95") for s in a2_sessions),
          f"retry: ACID — attempt 2 worker turns must all be session …lNLOee95, "
          f"but embedded attempt-1 events leaked in: {a2_sessions}")
    # attempt 2 real worker turns are the 12 events at lines 78-89; if the ~5 embedded
    # events (70-74) leaked, tool/step counts balloon. Bound it.
    check(len(worker_turns(a2, "tool")) == 3,
          f"retry: attempt 2 should have exactly 3 real tool turns, got {len(worker_turns(a2,'tool'))} "
          f"(embedded reproduction leaked if >3)")
    # the injected failure is ONE orchestrator reply turn, not exploded into worker events
    rr = orch_turns(a2, "retry_reply")
    check(len(rr) >= 1 and any("Previous attempt failed" in (t.get("text") or "") for t in rr),
          "retry: attempt 2 must carry an orchestrator retry_reply turn containing 'Previous attempt failed'")
    check(any((t.get("text") or "").startswith("Done.") for t in worker_turns(a2, "text")),
          "retry: attempt 2 final worker text ('Done. The file …') must be present")
except Exception as e:
    problems.append(f"retry: parser raised {e!r}")

# ---------- 3. MOCK (non-JSON engine) ----------
try:
    d = parse("mock.plainfile.log")
    check(d.get("engine") == "mock", f"mock: engine should be 'mock', got {d.get('engine')!r}")
    check(len(d.get("attempts", [])) == 1, f"mock: expected 1 attempt, got {len(d.get('attempts',[]))}")
    a = d["attempts"][0]
    check(a.get("outcome") == "success", f"mock: outcome should be success (rc=0), got {a.get('outcome')!r}")
    check(any("mock mechanics" in (t.get("text") or "") for t in orch_turns(a, "spec")),
          "mock: spec turn must be recovered even though there is no NDJSON")
    check(any("mock-worker: wrote" in (t.get("text") or "") for t in worker_turns(a)),
          "mock: the plain-text worker output must surface as a worker turn")
except Exception as e:
    problems.append(f"mock: parser raised {e!r}")

# ---------- 4. IN-PROGRESS (truncated mid-stream, no exit trailer) ----------
try:
    raw = (FX / "happy.opencode.log").read_text()
    # drop the "exited rc=" trailer and chop the final event line mid-way
    body = raw.split("[ringer.py] attempt 1 exited")[0]
    truncated = body[: int(len(body) * 0.85)]  # cut inside the last event line
    d = mod.parse_transcript(truncated)
    check(d.get("status") == "running", f"in-progress: status should be 'running', got {d.get('status')!r}")
    check(len(d.get("attempts", [])) == 1, "in-progress: should still yield the one running attempt")
    check(d["attempts"][0].get("outcome") == "running",
          f"in-progress: attempt outcome should be 'running', got {d['attempts'][0].get('outcome')!r}")
    # a truncated final JSON line must be tolerated (skipped + noted), never fatal
    check(isinstance(d.get("parse_warnings"), list), "in-progress: parse_warnings must be a list")
except Exception as e:
    problems.append(f"in-progress: parser raised {e!r} (must tolerate a truncated final line, not crash)")

if problems:
    print("FAIL:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("PASS: transcript parser produced correct, embedded-exclusion-safe transcripts for all fixtures")
PY
status=$?
test $status -eq 0 || exit $status
echo "transcript parser check: green"
