#!/bin/sh
# Check: the agent video-wall frontend is wired correctly — the JS parses, the required
# hooks exist, the page still serves with the server-side models-tab injection intact,
# and the :8787 per-run dashboard stays permanently killed. Runs from repo root.
set -u

HTML=dashboard/ringside.html
test -f "$HTML" || { echo "FAIL: $HTML missing"; exit 1; }
test -f ringer.py || { echo 'FAIL: run from ringer repo root'; exit 1; }

problems=""
add() { problems="$problems\n  - $1"; }

# 1. required frontend hooks (the data-* styling contract + the routes)
for hook in "agent video-wall" "renderTranscript" "applyBadge" "fetchWallData" \
            "/transcript/" "/live-model/" "screen-close" "served-badge" \
            "tile-body" "data-transcript-key" "data-badge-key" \
            'workers:has(.worker-card.expanded)'; do
  grep -qF "$hook" "$HTML" || add "missing frontend hook: $hook"
done

# 2. JS parses (extract the module script block, node --check it)
python3 - "$HTML" > /tmp/ringside_wall.js <<'PY'
import sys, re
html = open(sys.argv[1], encoding="utf-8").read()
blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
# emit the largest block (the app module)
print(max(blocks, key=len) if blocks else "")
PY
if command -v node >/dev/null 2>&1; then
  node --check /tmp/ringside_wall.js 2>/tmp/ringside_wall.err || {
    add "ringside.html JS failed node --check:"; problems="$problems\n$(sed 's/^/      /' /tmp/ringside_wall.err)"; }
else
  echo "note: node not found — skipping JS syntax check"
fi

# 3. page still serves and the models tab still injects (server-side)
python3 - <<'PY'
import sys, os
sys.path.insert(0, os.getcwd())
import ringer
try:
    html = ringer.read_ringside_html()
except Exception as e:
    print(f"PROBLEM: read_ringside_html raised {e}"); sys.exit(3)
injected = ringer.inject_models_tab_into_ringside_html(html)
ok = ('id="models-panel"' in injected) and ("renderTranscript" in injected) and ("/live-model/" in injected)
sys.exit(0 if ok else 4)
PY
case $? in
  0) : ;;
  3) add "read_ringside_html raised" ;;
  4) add "models-tab injection lost the wall hooks (models-panel/renderTranscript/live-model)" ;;
esac

# 4. :8787 per-run dashboard stays dead (no Dashboard(...) instantiation)
if grep -qE '^\s*Dashboard\(' ringer.py; then
  add ":8787 regression — ringer.py re-instantiates the per-run Dashboard server"
fi
grep -qF "self.dashboard = None" ringer.py || add ":8787 kill missing (expected 'self.dashboard = None')"

if [ -n "$problems" ]; then
  printf "FAIL:%b\n" "$problems"
  exit 1
fi
echo "wall frontend check: green (JS parses, hooks present, page serves, models-tab intact, :8787 dead)"
