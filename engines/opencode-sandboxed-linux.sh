#!/bin/bash
# Ringer engine wrapper: run OpenCode under a Linux bubblewrap sandbox.
# Linux/WSL counterpart of opencode-sandboxed.sh (macOS Seatbelt): full
# network and reads, writes confined to the task dir, a per-run scratch dir
# (wired as TMPDIR/XDG_CACHE_HOME), and OpenCode's own state/config dirs.
#
# Usage (as a ringer engine bin):
#   opencode-sandboxed-linux.sh <taskdir> [--no-sandbox] <opencode args...>
#
# The first argument is the task directory (pass "{taskdir}" first in
# args_template). "--no-sandbox" as the second argument skips bwrap entirely
# — wire it as the engine's full_access_args so ringer's allow_full_access
# gate still applies.
set -euo pipefail

TASKDIR="${1:?usage: opencode-sandboxed-linux.sh <taskdir> [--no-sandbox] <args...>}"; shift
SANDBOX=1
if [ "${1:-}" = "--no-sandbox" ]; then SANDBOX=0; shift; fi

# Resolve opencode without tripping `set -e` (command -v returns nonzero when absent).
if ! OPENCODE_BIN="$(command -v opencode)" || [ -z "$OPENCODE_BIN" ]; then
  OPENCODE_BIN="$HOME/.opencode/bin/opencode"
fi
if [ ! -x "$OPENCODE_BIN" ]; then
  echo "opencode-sandboxed-linux.sh: opencode not found on PATH or at ~/.opencode/bin" >&2
  exit 127
fi

if [ "$SANDBOX" = "0" ]; then
  exec "$OPENCODE_BIN" "$@" < /dev/null
fi

if ! BWRAP_BIN="$(command -v bwrap)" || [ -z "$BWRAP_BIN" ]; then
  echo "opencode-sandboxed-linux.sh: bwrap not found — install bubblewrap or use full-access mode (--no-sandbox)" >&2
  exit 1
fi

TASKDIR_REAL="$(cd "$TASKDIR" && pwd -P)"

# Per-run scratch root — becomes both TMPDIR and XDG_CACHE_HOME for OpenCode,
# so the read-only root never needs /tmp or ~/.cache opened up wholesale.
SCRATCH="$(mktemp -d -t ringer-opencode-scratch.XXXXXX)"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

OC_SHARE="$HOME/.local/share/opencode"
OC_STATE="$HOME/.local/state/opencode"
OC_CONFIG="$HOME/.config/opencode"
mkdir -p "$OC_SHARE" "$OC_STATE" "$OC_CONFIG" "$SCRATCH/cache"

export TMPDIR="$SCRATCH"
export XDG_CACHE_HOME="$SCRATCH/cache"

# Read-only root, then selective read-write binds. Network stays shared.
# Run as a child (not exec) so the EXIT trap fires and cleans up the scratch
# dir even on the success path; propagate the child's exit status.
set +e
"$BWRAP_BIN" \
  --ro-bind / / \
  --dev-bind /dev /dev \
  --proc /proc \
  --bind "$TASKDIR_REAL" "$TASKDIR_REAL" \
  --bind "$SCRATCH" "$SCRATCH" \
  --bind "$OC_SHARE" "$OC_SHARE" \
  --bind "$OC_STATE" "$OC_STATE" \
  --bind "$OC_CONFIG" "$OC_CONFIG" \
  --die-with-parent \
  "$OPENCODE_BIN" "$@" < /dev/null
status=$?
set -e
exit "$status"
