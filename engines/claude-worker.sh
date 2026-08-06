#!/bin/bash
# Ringer engine wrapper: run Claude Code headless (-p) as a worker, inside the task dir.
#
# Uses Anthropic's own Claude Code CLI on the user's Claude subscription — no OpenCode
# and no separate OAuth. Claude Code has no -C/--dir flag, so we cd into the task dir
# (its cwd becomes the write target), then exec claude with whatever args ringer passes.
#
# CONFINEMENT NOTE: --dangerously-skip-permissions gives Claude no OS-level sandbox
# (unlike codex --sandbox or the opencode Seatbelt wrapper). The only isolation is the
# per-task directory ringer assigns. KEEP SWARM workdirs OUTSIDE any repo you care about,
# and review artifacts before integrating. macOS/Linux.
set -euo pipefail
TASKDIR="${1:?usage: claude-worker.sh <taskdir> <claude args...>}"; shift
cd "$TASKDIR"
exec claude "$@" < /dev/null
