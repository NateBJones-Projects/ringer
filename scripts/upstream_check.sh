#!/bin/sh
# SessionStart hook: passively surface any NEW commits on the ORIGINAL upstream
# (origin = NateBJones-Projects/ringer) so Ringer-Claude stays aware of drift.
# We PUSH to the fork (ajo); we only WATCH origin. Fail-open: never block a
# session, never hang it — offline / slow network just prints nothing.
set -u
cd /home/ajo/ringer 2>/dev/null || exit 0

# Quiet, time-boxed fetch of the upstream only. If timeout(1) is missing or the
# network is down, we fall through to reporting against the last-known origin/main.
timeout 10 git fetch --quiet origin 2>/dev/null || true

# Count / list commits upstream has that our HEAD does not.
new=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
if [ "${new:-0}" -gt 0 ]; then
  echo "=== UPSTREAM DRIFT: original (origin/NateBJones-Projects/ringer) has ${new} new commit(s) not in our history ==="
  git log --oneline --no-decorate HEAD..origin/main 2>/dev/null | head -15
  echo "(review with: git log HEAD..origin/main ; we push to 'ajo', not origin)"
fi
exit 0
