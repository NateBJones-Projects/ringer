#!/usr/bin/env bash
# Per-worker OpenCode state isolation for Ringer's DIAL lane.
#
# WHY THIS EXISTS
# OpenCode keeps its session state in a SQLite database under XDG_DATA_HOME.
# That database is shared by every OpenCode process on the machine, so when
# Ringer starts several workers at once they race on it and the losers die
# immediately with:
#
#     Error: Unexpected error
#     database is locked
#
# Observed on 2026-09-02: 2 of 3 parallel workers crashed one second after
# launch, before any model call. Ringer's retry absorbed it and all three
# tasks still passed, but each loss costs an attempt and the odds get worse
# as --max-parallel rises.
#
# WHAT THIS DOES
# Gives each worker process a private XDG data/state/cache tree, so each gets
# its own SQLite database and they cannot contend. The config directory is
# deliberately NOT redirected: ~/.config/opencode/opencode.json holds the DIAL
# provider blocks and every worker must still read it. Credentials are not
# affected either — the DIAL key is injected via {env:DIAL_API_KEY}, not from
# OpenCode's auth store in the data directory.
#
# The private tree is removed when the worker exits normally. A worker killed
# by Ringer's timeout (process-group kill) may leave one behind under TMPDIR;
# they are small and safe to delete.
set -uo pipefail

state_dir="$(mktemp -d "${TMPDIR:-/tmp}/ringer-opencode-XXXXXXXX")"
cleanup() { rm -rf "$state_dir"; }
trap cleanup EXIT INT TERM

export XDG_DATA_HOME="$state_dir/data"
export XDG_STATE_HOME="$state_dir/state"
export XDG_CACHE_HOME="$state_dir/cache"
mkdir -p "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_CACHE_HOME"

# Never let a worker self-update in the middle of a swarm; that would change
# the engine's behaviour partway through a run and break reproducibility.
export OPENCODE_DISABLE_AUTOUPDATE=1

opencode "$@"
exit $?
