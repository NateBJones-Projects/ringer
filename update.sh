#!/usr/bin/env bash
# Update Ringer while preserving local customizations.
#
# WHY THIS EXISTS
# Ringer's built-in self-update refuses to touch a dirty tracked tree, and it
# says so deliberately: it "never creates a merge commit, never rebases, never
# stashes or deletes changes." This checkout carries local edits that are not
# upstream and never will be — the [engines.dial] block in
# registry/model-identity.toml and the dial sections in docs/MODEL-NOTES.md —
# so the built-in updater is permanently blocked and shows a banner instead.
#
# This wrapper does the stash/update/pop dance around it. Run this instead of
# waiting for the automatic check.
#
# SAFETY
# - Only TRACKED modifications are stashed. Untracked files (the swarm
#   manifests, engines/opencode-dial.sh, .python-version) never blocked the
#   update and are left exactly where they are.
# - If the pop conflicts, the stash is KEPT and the script exits non-zero with
#   instructions. Your work is never dropped on a conflict.
# - Refuses to run off main, because that is the only branch Ringer updates on.
set -uo pipefail

cd "$(dirname "$0")" || { echo "update.sh: cannot enter the ringer checkout"; exit 1; }

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ "$branch" != "main" ]; then
  echo "update.sh: on branch '$branch', but Ringer only self-updates on main."
  echo "           Switch to main first, or update manually."
  exit 1
fi

stashed=0
if ! git diff --quiet HEAD -- 2>/dev/null; then
  echo "update.sh: stashing local tracked changes..."
  git diff --stat HEAD -- | sed 's/^/           /'
  if ! git stash push -m "update.sh autostash $(date -Iseconds)"; then
    echo "update.sh: git stash failed; refusing to update over a dirty tree."
    exit 1
  fi
  stashed=1
else
  echo "update.sh: no local tracked changes to stash."
fi

echo "update.sh: checking for an upstream update..."
./ringer.py self-update
update_rc=$?

if [ "$stashed" -eq 1 ]; then
  echo "update.sh: restoring local changes..."
  if git stash pop; then
    echo "update.sh: local changes restored cleanly."
  else
    echo ""
    echo "update.sh: CONFLICT while restoring your local changes."
    echo "           Nothing is lost — the stash is still there. To see it:"
    echo "             git stash list"
    echo "           Resolve the conflicts shown above, then drop the stash:"
    echo "             git stash drop"
    echo "           Most likely cause: upstream edited the same tail of"
    echo "           registry/model-identity.toml or docs/MODEL-NOTES.md that"
    echo "           the local dial sections append to."
    exit 2
  fi
fi

exit $update_rc
