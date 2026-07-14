#!/bin/sh
# Check: the HUD's new /transcript and /live-model routes serve valid JSON, reuse the
# existing path-containment hardening (bogus + traversal -> 404), and fail open on the
# live route (no Feeder session -> 200 with live=false, never 500). Runs from repo root
# (or a worktree). POSIX/dash-safe.
set -u

test -f ringer.py || { echo 'FAIL: run from the ringer repo root'; exit 1; }
FX=tests/fixtures/worker-logs/happy.opencode.log
test -f "$FX" || FX=/home/ajo/ringer/tests/fixtures/worker-logs/happy.opencode.log
test -f "$FX" || { echo "FAIL: missing fixture happy.opencode.log"; exit 1; }
export RINGER_FIXTURE_LOG="$(cd "$(dirname "$FX")" && pwd)/$(basename "$FX")"

python3 - <<'PY'
import sys, os, json, tempfile, pathlib, urllib.request

sys.path.insert(0, os.getcwd())
import ringer

fixture_log = os.environ["RINGER_FIXTURE_LOG"]
problems = []
def check(c, m):
    if not c: problems.append(m)

def get(port, path):
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, ""

with tempfile.TemporaryDirectory() as d:
    state_dir = pathlib.Path(d)
    runs = state_dir / "runs"; runs.mkdir()
    run_id = "wall-endpoints-test"
    (runs / f"{run_id}.json").write_text(json.dumps({
        "run_id": run_id, "run_name": "wall-endpoints",
        "tasks": [{"key": "alpha", "status": "pass", "model": "feeder/auto/coding",
                   "log_path": fixture_log}],
    }))

    server = ringer.PersistentHudServer(state_dir, preferred_port=0, open_viewer=False)
    port = server.start()
    try:
        # /transcript happy path
        st, body = get(port, f"/transcript/{run_id}/alpha")
        check(st == 200, f"/transcript returned {st}, expected 200")
        try:
            d1 = json.loads(body)
        except Exception as e:
            d1 = None; problems.append(f"/transcript body not JSON: {e}")
        if d1 is not None:
            check(d1.get("schema") == 1, "/transcript: schema should be 1")
            check(len(d1.get("attempts", [])) == 1, "/transcript: expected 1 attempt from happy fixture")
            turns = d1["attempts"][0]["turns"] if d1.get("attempts") else []
            check(any(t.get("role") == "orchestrator" for t in turns),
                  "/transcript: attempt must include the orchestrator spec turn")
            check(d1.get("tokens_total") == 7314, f"/transcript: tokens_total should be 7314, got {d1.get('tokens_total')}")

        # bogus task key -> 404
        st, _ = get(port, f"/transcript/{run_id}/nope")
        check(st == 404, f"/transcript bogus task should 404, got {st}")

        # run_id path traversal -> 404 (containment reused)
        st, _ = get(port, "/transcript/..%2f..%2fetc%2fpasswd/alpha")
        check(st == 404, f"/transcript traversal run_id should 404, got {st}")

        # /live-model on the real fixture session: 200 + valid JSON. live may be True
        # (Feeder still has this historical session) or False (pending) — both valid;
        # if True, the served-model structure must be well-formed.
        st, body = get(port, f"/live-model/{run_id}/alpha")
        check(st == 200, f"/live-model returned {st}, expected 200")
        try:
            d2 = json.loads(body)
            check("served" in d2 and "live" in d2, "/live-model: must have 'served' and 'live' keys")
            if d2.get("live") is True:
                check(len(d2.get("served", [])) >= 1, "/live-model: live=True must carry served rows")
                cur = d2.get("current")
                check(isinstance(cur, dict) and cur.get("served_model"),
                      "/live-model: live=True must carry current.served_model")
                print(f"  (live served model resolved from Feeder: {d2['current']['served_model']})")
        except Exception as e:
            problems.append(f"/live-model body not JSON: {e}")

        # /live-model bogus task -> fail-open 200 with live=false (no_log), never 500
        st, body = get(port, f"/live-model/{run_id}/nope")
        check(st == 200, f"/live-model bogus task should be 200 fail-open, got {st}")
        try:
            d3 = json.loads(body)
            check(d3.get("live") is False, "/live-model bogus task: live should be False (no_log)")
        except Exception as e:
            problems.append(f"/live-model bogus body not JSON: {e}")
    finally:
        server.stop()

if problems:
    print("FAIL:")
    for p in problems: print("  -", p)
    sys.exit(1)
print("PASS: /transcript and /live-model serve valid JSON, enforce containment, and fail open")
PY
status=$?
test $status -eq 0 || exit $status
echo "wall endpoints check: green"
