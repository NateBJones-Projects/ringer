#!/bin/sh
# opencode-feeder.sh — per-invocation OpenCode state isolation for parallel
# Ringer workers on Linux/WSL.
#
# WHY: OpenCode keeps a shared SQLite state DB at XDG_DATA_HOME/opencode/
# opencode.db; concurrent workers race on it and die with "database is locked"
# (bit the phase4-feeder-telemetry round-1 swarm, 2026-07-14). Each invocation
# gets a private XDG_DATA_HOME instead. XDG_CONFIG_HOME is untouched, so the
# global feeder provider config (~/.config/opencode/opencode.json) still loads.
#
# This wrapper is Ringer's [engines.opencode] bin on this machine. It is NOT a
# sandbox (the macOS Seatbelt wrapper does not run here) — containment stays
# worktrees + scoped manifests + the human consequence-gate.
set -u

# opportunistic cleanup of state dirs older than 4h
find "${TMPDIR:-/tmp}" -maxdepth 1 -name 'ringer-oc-state.*' -mmin +240 \
  -exec rm -rf {} + 2>/dev/null || true

STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ringer-oc-state.XXXXXX")"
XDG_DATA_HOME="$STATE_DIR" exec /home/ajo/.opencode/bin/opencode "$@"
