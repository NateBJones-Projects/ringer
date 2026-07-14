#!/bin/sh
# opencode-env-session-probe.sh — proves how a Ringer worker gets a per-worker
# session identity on the wire, for feeder session-sticky routing.
# No model is called: the provider's baseURL is a local capture sink.
#
# FINDINGS THIS PROBE ASSERTS (established 2026-07-14 against OpenCode 1.17.20):
#   1. OpenCode NATIVELY stamps X-Session-Id (+ X-Session-Affinity) on every
#      provider request; the id is STABLE across all calls of one `opencode run`
#      invocation (main + title-gen) and DISTINCT across invocations.
#      => 1 worker attempt = 1 session id, with zero client plumbing.
#   2. {env:VAR} placeholders in provider `options.headers` are NOT substituted
#      (the header is clobbered/replaced by OpenCode's own session header) —
#      env-var header injection is NOT a usable mechanism for worker identity.
#   3. Provider config loads via OPENCODE_CONFIG env var (and equally via a
#      project opencode.json in --dir); options.apiKey flows as the Bearer token;
#      the wire model is the model key verbatim (provider/ prefix stripped).
set -u
OPENCODE_BIN=/home/ajo/.opencode/bin/opencode
PORT="${PROBE_PORT:-39131}"
BASE="$PWD"
CAP="$BASE/requests.jsonl"
: > "$CAP"

python3 /home/ajo/ringer/scripts/probes/header_sink.py "$PORT" "$CAP" &
SINK=$!
trap 'kill $SINK 2>/dev/null' EXIT
sleep 1

python3 - "$BASE" "$PORT" <<'PY'
import json, pathlib, sys
base, port = sys.argv[1], sys.argv[2]
cfg = {
    "$schema": "https://opencode.ai/config.json",
    "provider": {
        "sesstest": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "SessTest",
            "options": {
                "baseURL": f"http://127.0.0.1:{port}/v1",
                "apiKey": "probe-key",
                "headers": {
                    "X-Session-Id": "{env:RINGER_SESSION_ID}",
                    "X-Consumer": "ringer-probe",
                },
            },
            "models": {"auto/coding": {"name": "probe"}},
        }
    },
}
pathlib.Path(base, "oc-probe.json").write_text(json.dumps(cfg))
PY

echo "--- two invocations, distinct RINGER_SESSION_ID env values set (expected NOT to substitute) ---"
RINGER_SESSION_ID=probe-alpha OPENCODE_CONFIG="$BASE/oc-probe.json" timeout 60 \
  "$OPENCODE_BIN" run -m sesstest/auto/coding --dir "$BASE" "Reply with the single word ok" \
  > "$BASE/oc-a.log" 2>&1
RINGER_SESSION_ID=probe-beta OPENCODE_CONFIG="$BASE/oc-probe.json" timeout 60 \
  "$OPENCODE_BIN" run -m sesstest/auto/coding --dir "$BASE" "Reply with the single word ok" \
  > "$BASE/oc-b.log" 2>&1

kill $SINK 2>/dev/null
trap - EXIT

echo "--- captured wire evidence ---"
python3 - "$CAP" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print(f"captured {len(rows)} request(s) across 2 invocations")
if not rows:
    print("FAIL: nothing reached the sink — provider config never loaded (OPENCODE_CONFIG broken?)")
    sys.exit(1)
sids = [r["headers"].get("x-session-id", "<absent>") for r in rows]
distinct = sorted(set(sids))
models = sorted({(r.get("body") or {}).get("model", "<absent>") for r in rows})
auths = sorted({r["headers"].get("authorization", "<absent>") for r in rows})
affinity_matches = all(
    r["headers"].get("x-session-affinity") == r["headers"].get("x-session-id") for r in rows
)
print("distinct X-Session-Id values:", distinct)
print("wire model values:", models)
print("authorization values:", auths)
print("X-Session-Affinity == X-Session-Id on every request:", affinity_matches)
problems = []
if len(distinct) != 2:
    problems.append(
        f"expected exactly 2 session ids (1 per invocation, stable within), saw {len(distinct)}"
    )
if not all(s.startswith("ses_") for s in distinct):
    problems.append("session ids are not OpenCode-native ses_* values")
if any("probe-alpha" in s or "probe-beta" in s or "{env:" in s for s in distinct):
    problems.append("env placeholder unexpectedly substituted/leaked — mechanism model changed, re-verify")
if models != ["auto/coding"]:
    problems.append("wire model is not exactly auto/coding (prefix-strip broken?)")
if auths != ["Bearer probe-key"]:
    problems.append("configured apiKey did not flow as the Bearer token")
if problems:
    print("VERDICT: FAIL —", "; ".join(problems))
    sys.exit(1)
print("VERDICT: PASS — OpenCode natively stamps one stable, per-invocation X-Session-Id")
print("on every provider request (env-var header injection confirmed NOT substituted);")
print("1 worker attempt = 1 session id with zero client plumbing.")
PY
