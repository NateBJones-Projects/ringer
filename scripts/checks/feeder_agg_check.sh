#!/bin/sh
# Check: scripts/feeder_agg.py is the single source of truth for turning Feeder
# /api/requests rows into a served-model summary. aggregate_rows() must reproduce
# feeder_enrich.py's historical aggregation byte-for-byte; latest_served() must
# pick the last SUCCESS row (feeder-claude: failover rows exist, rows[last] is wrong);
# fetch_session_rows() must accept both the bare-array and {"requests":[...]} shapes.
# cwd = task worktree (or repo root). POSIX/dash-safe.
set -u

test -f scripts/feeder_agg.py || { echo 'FAIL: scripts/feeder_agg.py does not exist'; exit 1; }

python3 - <<'PY'
import sys, json, importlib.util, tempfile, pathlib

spec = importlib.util.spec_from_file_location("feeder_agg", "scripts/feeder_agg.py")
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(f"FAIL: scripts/feeder_agg.py did not import: {e!r}"); sys.exit(1)

for fn in ("aggregate_rows", "fetch_session_rows", "latest_served"):
    if not hasattr(mod, fn):
        print(f"FAIL: feeder_agg.py must expose {fn}()"); sys.exit(1)

problems = []
def check(c, m):
    if not c: problems.append(m)

# Rows in chronological order (as feeder returns them: oldest->newest).
# A 2-call sambanova/DeepSeek stretch, a failover to openrouter/nemotron, then a
# 429 and a trailing error — so the last row is NOT the served model.
rows = [
 {"platform":"sambanova","model_id":"DeepSeek-V3.1","served_model":"sambanova/DeepSeek-V3.1","status":"success","output_tokens":20,"latency_ms":600,"created_at":"2026-07-14T12:00:01Z"},
 {"platform":"sambanova","model_id":"DeepSeek-V3.1","served_model":"sambanova/DeepSeek-V3.1","status":"success","output_tokens":30,"latency_ms":800,"created_at":"2026-07-14T12:00:02Z"},
 {"platform":"openrouter","model_id":"nemotron","served_model":"openrouter/nemotron","status":"success","output_tokens":10,"latency_ms":400,"created_at":"2026-07-14T12:00:03Z"},
 {"platform":"minimax","model_id":"minimax","served_model":"minimax/minimax","status":"429","latency_ms":100,"created_at":"2026-07-14T12:00:04Z"},
 {"platform":"openrouter","model_id":"nemotron","served_model":"openrouter/nemotron","status":"error","created_at":"2026-07-14T12:00:05Z"},
]

# ---- aggregate_rows: must match feeder_enrich's historical shape/values ----
try:
    agg = mod.aggregate_rows(rows)
    check(agg.get("served") == [
        {"platform":"sambanova","model_id":"DeepSeek-V3.1","calls":2,"output_tokens":50},
        {"platform":"openrouter","model_id":"nemotron","calls":1,"output_tokens":10},
    ], f"aggregate_rows.served wrong: {agg.get('served')!r}")
    check(agg.get("failovers") == 1, f"failovers should be 1 (one platform change across success rows), got {agg.get('failovers')!r}")
    check(agg.get("mixed_models") is True, f"mixed_models should be True, got {agg.get('mixed_models')!r}")
    check(agg.get("requests") == 5, f"requests should be 5 (all rows), got {agg.get('requests')!r}")
    check(agg.get("errors_429") == 1, f"errors_429 should be 1, got {agg.get('errors_429')!r}")
    check(agg.get("latency_ms_total") == 1900, f"latency_ms_total should be 1900, got {agg.get('latency_ms_total')!r}")
    check(agg.get("latency_ms_p50") == 500.0, f"latency_ms_p50 should be 500.0, got {agg.get('latency_ms_p50')!r}")
except Exception as e:
    problems.append(f"aggregate_rows raised {e!r}")

# ---- latest_served: last SUCCESS row, not rows[-1] ----
try:
    ls = mod.latest_served(rows)
    check(ls is not None, "latest_served returned None on rows containing successes")
    if ls:
        check(ls.get("served_model") == "openrouter/nemotron",
              f"latest_served should be the LAST success (openrouter/nemotron), got {ls.get('served_model')!r}")
    check(mod.latest_served([]) is None, "latest_served([]) must be None (pending)")
    check(mod.latest_served([{"status":"429"}]) is None, "latest_served with no success rows must be None (still churning)")
except Exception as e:
    problems.append(f"latest_served raised {e!r}")

# ---- fetch_session_rows: both response shapes via fixture ----
try:
    with tempfile.TemporaryDirectory() as d:
        arr = pathlib.Path(d)/"arr.json"; arr.write_text(json.dumps(rows))
        wrapped = pathlib.Path(d)/"wrapped.json"; wrapped.write_text(json.dumps({"requests": rows}))
        r1 = mod.fetch_session_rows("ses_x", fixture=str(arr))
        r2 = mod.fetch_session_rows("ses_x", fixture=str(wrapped))
        check(isinstance(r1, list) and len(r1) == 5, f"fetch_session_rows(bare array) should return 5 rows, got {r1!r}")
        check(isinstance(r2, list) and len(r2) == 5, f"fetch_session_rows({{requests:[]}}) should return 5 rows, got {r2!r}")
except Exception as e:
    problems.append(f"fetch_session_rows raised {e!r}")

if problems:
    print("FAIL:")
    for p in problems: print("  -", p)
    sys.exit(1)
print("PASS: feeder_agg aggregate/latest_served/fetch all correct")
PY
status=$?
test $status -eq 0 || exit $status

# ---- regression: refactored feeder_enrich still byte-identical on the phase4 fixture ----
if [ -f scripts/checks/phase4_enrich_check.sh ]; then
  echo "feeder_agg: running phase4 enrich regression guard..."
  sh scripts/checks/phase4_enrich_check.sh || { echo 'FAIL: phase4 enrich regression broke after feeder_agg refactor'; exit 1; }
fi
echo "feeder_agg check: green"
