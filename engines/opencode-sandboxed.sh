#!/bin/bash
# Ringer engine wrapper: run OpenCode under a macOS Seatbelt sandbox.
#
# OpenCode has no OS-level sandbox of its own — its --dangerously-skip-permissions
# flag (required for headless runs) disables ALL of its interactive approval
# prompts. This wrapper supplies the real containment: full network and reads,
# writes confined to the task dir, temp dirs, and OpenCode's own state dirs.
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

TASKDIR="$1"; shift
SANDBOX=1
if [ "${1:-}" = "--no-sandbox" ]; then SANDBOX=0; shift; fi

OPENCODE_BIN="$(command -v opencode)"
if [ -z "$OPENCODE_BIN" ]; then
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
PROFILE="$(mktemp -t ringer-opencode-sb).sb"
trap 'rm -f "$PROFILE"' EXIT

cat > "$PROFILE" <<SBEOF
(version 1)
(allow default)
(deny file-write*)
(allow file-write*
  (subpath "$TASKDIR_REAL")
  (subpath "/private/tmp")
  (subpath "/private/var/folders")
  (subpath "/dev")
  (subpath "$HOME/.local/share/opencode")
  (subpath "$HOME/.local/state/opencode")
  (subpath "$HOME/.cache")
  (subpath "$HOME/.config/opencode"))
SBEOF

exec /usr/bin/sandbox-exec -f "$PROFILE" "$OPENCODE_BIN" "$@" < /dev/null
