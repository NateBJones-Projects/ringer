#!/bin/bash
# Run OpenCode with host reads available, writes confined to the task and
# ephemeral per-run state, and the inherited environment removed.
set -euo pipefail

TASKDIR="${1:?usage: opencode-sandboxed-linux.sh <taskdir> [--no-sandbox] <args...>}"
shift
SANDBOX=1
if [ "${1:-}" = "--no-sandbox" ]; then
  SANDBOX=0
  shift
fi

if [ -n "${OPENCODE_BIN:-}" ]; then
  :
elif [ -x "$HOME/.opencode/bin/opencode" ]; then
  OPENCODE_BIN="$HOME/.opencode/bin/opencode"
elif ! OPENCODE_BIN="$(command -v opencode)" || [ -z "$OPENCODE_BIN" ]; then
  echo "opencode-sandboxed-linux.sh: native opencode not found" >&2
  echo "Install it with: curl -fsSL https://opencode.ai/install | bash" >&2
  exit 127
fi

if [ ! -x "$OPENCODE_BIN" ]; then
  echo "opencode-sandboxed-linux.sh: not executable: $OPENCODE_BIN" >&2
  exit 127
fi

TASKDIR_REAL="$(cd "$TASKDIR" && pwd -P)"
if [ "$SANDBOX" = "0" ]; then
  cd "$TASKDIR_REAL"
  exec "$OPENCODE_BIN" "$@" < /dev/null
fi

if ! BWRAP_BIN="$(command -v bwrap)" || [ -z "$BWRAP_BIN" ]; then
  echo "opencode-sandboxed-linux.sh: bubblewrap is required for sandboxed runs" >&2
  echo "Install it with: sudo apt-get install bubblewrap" >&2
  exit 1
fi

SCRATCH="$(mktemp -d -t ringer-opencode-linux.XXXXXX)"
cleanup() {
  rm -rf -- "$SCRATCH"
}
trap cleanup EXIT

SANDBOX_HOME="$SCRATCH/home"
SANDBOX_TMP="$SCRATCH/tmp"
mkdir -p \
  "$SANDBOX_HOME/.cache" \
  "$SANDBOX_HOME/.config/opencode" \
  "$SANDBOX_HOME/.local/share/opencode" \
  "$SANDBOX_HOME/.local/state/opencode" \
  "$SANDBOX_TMP"

AUTH_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/opencode/auth.json"
if [ -f "$AUTH_FILE" ]; then
  install -m 600 "$AUTH_FILE" "$SANDBOX_HOME/.local/share/opencode/auth.json"
fi

OPENCODE_DIR="$(cd "$(dirname "$OPENCODE_BIN")" && pwd -P)"

set +e
"$BWRAP_BIN" \
  --die-with-parent \
  --new-session \
  --unshare-user \
  --unshare-pid \
  --unshare-uts \
  --unshare-ipc \
  --share-net \
  --clearenv \
  --ro-bind / / \
  --proc /proc \
  --dev /dev \
  --bind "$SCRATCH" "$SCRATCH" \
  --bind "$TASKDIR_REAL" "$TASKDIR_REAL" \
  --setenv HOME "$SANDBOX_HOME" \
  --setenv PATH "$OPENCODE_DIR:/usr/local/bin:/usr/bin:/bin" \
  --setenv TMPDIR "$SANDBOX_TMP" \
  --setenv XDG_CACHE_HOME "$SANDBOX_HOME/.cache" \
  --setenv XDG_CONFIG_HOME "$SANDBOX_HOME/.config" \
  --setenv XDG_DATA_HOME "$SANDBOX_HOME/.local/share" \
  --setenv XDG_STATE_HOME "$SANDBOX_HOME/.local/state" \
  --setenv LANG C.UTF-8 \
  --chdir "$TASKDIR_REAL" \
  "$OPENCODE_BIN" "$@" < /dev/null
status=$?
set -e
exit "$status"
