#!/bin/bash
# Ringer engine wrapper: run OpenCode under a macOS Seatbelt sandbox.
#
# OpenCode has no OS-level sandbox of its own — its --dangerously-skip-permissions
# flag (required for headless runs) disables ALL of its interactive approval
# prompts. This wrapper supplies the real containment: full network and reads,
# writes confined to the task dir, a per-run scratch/cache dir, and OpenCode's
# own state dirs. The scratch root also carries a private per-run XDG_DATA_HOME,
# which is what keeps concurrent workers off one shared session database (see the
# comment on that export below — it is a correctness fix for parallel runs).
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
  # Full-access mode keeps OpenCode's real ~/.local/share/opencode, so it also
  # keeps the shared-session-DB contention documented below. Fine for a lone
  # full-access task; do not fan out several at once.
  exec "$OPENCODE_BIN" "$@" < /dev/null
fi

if [ ! -x /usr/bin/sandbox-exec ]; then
  echo "opencode-sandboxed.sh: /usr/bin/sandbox-exec not available (macOS only)." >&2
  echo "Use the engine's full-access mode (--no-sandbox) or add your own sandbox." >&2
  exit 1
fi

TASKDIR_REAL="$(cd "$TASKDIR" && pwd -P)"

# Per-run scratch root — becomes both TMPDIR and XDG_CACHE_HOME for OpenCode, so
# we never have to open all of /private/tmp or ~/.cache to the sandboxed agent.
# Resolve to the real path (/var/folders symlinks to /private/var/folders);
# Seatbelt subpath matching needs the canonical path or writes EPERM-crash.
SCRATCH="$(cd "$(mktemp -d -t ringer-opencode-scratch)" && pwd -P)"
PROFILE="$(mktemp -t ringer-opencode-prof)"
cleanup() { rm -rf "$SCRATCH" "$PROFILE"; }
trap cleanup EXIT

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
  (subpath (param "OC_CONFIG"))
  (subpath (param "OC_BASE")))
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

# Per-run DATA root — this is a concurrency fix, not just containment. OpenCode
# keeps its session store at $XDG_DATA_HOME/opencode/opencode.db, defaulting to
# ~/.local/share/opencode: one file, shared by every worker, opened for write,
# growing without bound. On 2026-07-27 it stood at 1.78 GB and two of three
# workers launched simultaneously died before their first tool call with
# "Error: Unexpected error / database is locked" — recorded as model failures
# though no model had run. Since every non-Codex model routes through this
# engine, shared-DB contention penalises exactly the cheap tier, and worsens as
# parallelism rises. A private store per worker removes the contention entirely.
# Credentials live beside the DB, so seed the fresh root with auth.json — copy,
# never symlink: the profile denies writes outside SCRATCH, and a symlink would
# resolve straight back to the shared file this exists to avoid.
export XDG_DATA_HOME="$SCRATCH/share"
mkdir -p "$XDG_DATA_HOME/opencode"
if [ -f "$HOME/.local/share/opencode/auth.json" ]; then
  cp "$HOME/.local/share/opencode/auth.json" "$XDG_DATA_HOME/opencode/auth.json"
  chmod 600 "$XDG_DATA_HOME/opencode/auth.json"
fi

# Run as a child (not exec) so the EXIT trap fires and cleans up the profile +
# scratch dir even on the success path; propagate the child's exit status.
set +e
/usr/bin/sandbox-exec \
  -D "TASKDIR=$TASKDIR_REAL" \
  -D "SCRATCH=$SCRATCH" \
  -D "OC_SHARE=$HOME/.local/share/opencode" \
  -D "OC_STATE=$HOME/.local/state/opencode" \
  -D "OC_CONFIG=$HOME/.config/opencode" \
  -D "OC_BASE=$HOME/.opencode" \
  -f "$PROFILE" "$OPENCODE_BIN" "$@" < /dev/null
status=$?
set -e
exit "$status"
