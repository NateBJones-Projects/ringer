#!/bin/bash
# Ringer engine wrapper: run OpenCode under a macOS Seatbelt sandbox.
#
# OpenCode has no OS-level sandbox of its own — its --dangerously-skip-permissions
# flag (required for headless runs) disables ALL of its interactive approval
# prompts. This wrapper supplies the real containment: full network and reads,
# writes confined to the task dir, a per-run scratch/cache dir, and OpenCode's
# own state dirs.
#
# Usage (as a ringer engine bin):
#   opencode-sandboxed.sh <taskdir> [--no-sandbox] <opencode args...>
#
# The first argument is the task directory (pass "{taskdir}" first in
# args_template). "--no-sandbox" as the second argument skips Seatbelt entirely
# — wire it as the engine's full_access_args so ringer's allow_full_access gate
# still applies. macOS only (sandbox-exec); on other platforms only
# --no-sandbox mode works.
set -euo pipefail

TASKDIR="${1:?usage: opencode-sandboxed.sh <taskdir> [--no-sandbox] <args...>}"; shift
SANDBOX=1
if [ "${1:-}" = "--no-sandbox" ]; then SANDBOX=0; shift; fi

# Resolve opencode without tripping `set -e` (command -v returns nonzero when absent).
if ! OPENCODE_BIN="$(command -v opencode)" || [ -z "$OPENCODE_BIN" ]; then
  echo "opencode-sandboxed.sh: opencode not found on PATH" >&2
  exit 127
fi

if [ "$SANDBOX" = "0" ]; then
  exec "$OPENCODE_BIN" "$@" < /dev/null
fi

if [ ! -x /usr/bin/sandbox-exec ]; then
  echo "opencode-sandboxed.sh: /usr/bin/sandbox-exec not available (macOS only)." >&2
  echo "Use the engine's full-access mode (--no-sandbox) or add your own sandbox." >&2
  exit 1
fi

TASKDIR_REAL="$(cd "$TASKDIR" && pwd -P)"

# Set up cleanup before creating any temp files so an early crash or signal
# doesn't leak them. The function body is updated below once paths are known.
cleanup() {
  # Guard against uninitialized vars during early termination. Tolerate rm
  # failures so `set -e` can't abort the trap mid-run or replace the script's
  # intended exit status; always end cleanly.
  [ -n "${SCRATCH:-}" ] && rm -rf "$SCRATCH" || true
  [ -n "${PROFILE:-}" ] && rm -f "$PROFILE" || true
}
trap cleanup EXIT

# Per-run scratch root — becomes both TMPDIR and XDG_CACHE_HOME for OpenCode, so
# we never have to open all of /private/tmp or ~/.cache to the sandboxed agent.
# Resolve to the real path (/var/folders symlinks to /private/var/folders);
# Seatbelt subpath matching needs the canonical path or writes EPERM-crash.
# NOTE: mktemp runs inside `if ! ...` so a failure surfaces our diagnostic
# instead of `set -e` exiting the script silently; and SCRATCH is pre-assigned
# before the canonicalizing cd/pwd so the EXIT trap still removes the temp dir
# even if the validation or the cd fails (previously SCRATCH_TMP leaked in
# those paths). The canonical path resolves into SCRATCH_REAL and is only
# promoted to SCRATCH on success, so a failed cd can never clobber SCRATCH.
if ! SCRATCH_TMP="$(mktemp -d -t ringer-opencode-scratch)"; then
  echo "opencode-sandboxed.sh: mktemp -d failed for scratch directory" >&2
  exit 1
fi
SCRATCH="$SCRATCH_TMP"  # trap-cleaned from here on, even if the cd below fails
if [ -z "$SCRATCH_TMP" ] || [ ! -d "$SCRATCH_TMP" ]; then
  echo "opencode-sandboxed.sh: mktemp -d returned invalid scratch directory" >&2
  exit 1
fi
if ! SCRATCH_REAL="$(cd "$SCRATCH_TMP" && pwd -P)"; then
  echo "opencode-sandboxed.sh: cannot resolve scratch dir: $SCRATCH_TMP" >&2
  exit 1
fi
SCRATCH="$SCRATCH_REAL"

# Same set -e-safe pattern as the scratch mktemp above: run mktemp inside
# `if ! ...` so a failure surfaces our diagnostic instead of exiting silently.
if ! PROFILE="$(mktemp -t ringer-opencode-prof)"; then
  echo "opencode-sandboxed.sh: mktemp failed for profile file" >&2
  exit 1
fi
if [ -z "$PROFILE" ] || [ ! -f "$PROFILE" ]; then
  echo "opencode-sandboxed.sh: mktemp returned invalid profile file" >&2
  exit 1
fi

# Paths are passed to the profile via sandbox-exec -D parameters, NOT string
# interpolation — a task dir containing quotes/parens/newlines can't inject rules.
cat > "$PROFILE" <<'SBEOF'
(version 1)
(allow default)
(deny file-write*)
(allow file-write*
  (subpath (param "TASKDIR"))
  (subpath (param "SCRATCH"))
  (subpath (param "OC_SHARE"))
  (subpath (param "OC_STATE"))
  (subpath (param "OC_CONFIG")))
; /dev is needed for /dev/null, /dev/urandom, etc.; writes there can't create
; persistent files without root, so a few literals are allowed rather than via param.
(allow file-write-data
  (literal "/dev/null")
  (literal "/dev/dtracehelper")
  (literal "/dev/tty"))
SBEOF

export TMPDIR="$SCRATCH"
export XDG_CACHE_HOME="$SCRATCH/cache"
mkdir -p "$XDG_CACHE_HOME"

# Run as a child (not exec) so the EXIT trap fires and cleans up the profile +
# scratch dir even on the success path; propagate the child's exit status.
set +e
/usr/bin/sandbox-exec \
  -D "TASKDIR=$TASKDIR_REAL" \
  -D "SCRATCH=$SCRATCH" \
  -D "OC_SHARE=$HOME/.local/share/opencode" \
  -D "OC_STATE=$HOME/.local/state/opencode" \
  -D "OC_CONFIG=$HOME/.config/opencode" \
  -f "$PROFILE" "$OPENCODE_BIN" "$@" < /dev/null
status=$?
set -e
exit "$status"
