#!/bin/bash
# Ringer engine wrapper: run OpenCode under a Linux/WSL2 bubblewrap sandbox.
#
# Usage (as a Ringer engine bin):
#   opencode-sandboxed-linux.sh <taskdir> [--no-sandbox] <opencode args...>
#
# Sandboxed mode exposes a minimal read-only system, maps only the task directory
# read/write at /workspace, uses an ephemeral HOME, clears inherited environment
# variables, and copies OpenCode auth into that ephemeral HOME with mode 600.
# Network remains available for the model provider. Full-access mode is an
# explicit --no-sandbox escape wired through Ringer's allow_full_access gate.
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
  rewritten=()
  for arg in "$@"; do
    if [ "$arg" = "/workspace" ]; then
      rewritten+=("$TASKDIR_REAL")
    else
      rewritten+=("$arg")
    fi
  done
  cd "$TASKDIR_REAL"
  exec "$OPENCODE_BIN" "${rewritten[@]}" < /dev/null
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
mkdir -p \
  "$SANDBOX_HOME/.cache" \
  "$SANDBOX_HOME/.config/opencode" \
  "$SANDBOX_HOME/.local/share/opencode" \
  "$SANDBOX_HOME/.local/state/opencode"

AUTH_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/opencode/auth.json"
if [ -f "$AUTH_FILE" ]; then
  install -m 600 "$AUTH_FILE" "$SANDBOX_HOME/.local/share/opencode/auth.json"
fi

OPENCODE_DIR="$(cd "$(dirname "$OPENCODE_BIN")" && pwd -P)"
OPENCODE_NAME="$(basename "$OPENCODE_BIN")"
sandbox_args=()
for arg in "$@"; do
  if [ "$arg" = "$TASKDIR_REAL" ]; then
    sandbox_args+=("/workspace")
  else
    sandbox_args+=("$arg")
  fi
done

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
  --ro-bind /usr /usr \
  --ro-bind-try /bin /bin \
  --ro-bind-try /sbin /sbin \
  --ro-bind-try /lib /lib \
  --ro-bind-try /lib64 /lib64 \
  --ro-bind /etc /etc \
  --ro-bind-try /sys /sys \
  --dir /mnt \
  --ro-bind-try /mnt/wsl /mnt/wsl \
  --proc /proc \
  --dev /dev \
  --tmpfs /tmp \
  --dir /opt \
  --ro-bind "$OPENCODE_DIR" /opt/opencode \
  --dir /home \
  --bind "$SANDBOX_HOME" /home/ringer \
  --dir /workspace \
  --bind "$TASKDIR_REAL" /workspace \
  --setenv HOME /home/ringer \
  --setenv PATH /opt/opencode:/usr/local/bin:/usr/bin:/bin \
  --setenv TMPDIR /tmp \
  --setenv XDG_CACHE_HOME /home/ringer/.cache \
  --setenv XDG_CONFIG_HOME /home/ringer/.config \
  --setenv XDG_DATA_HOME /home/ringer/.local/share \
  --setenv XDG_STATE_HOME /home/ringer/.local/state \
  --setenv LANG C.UTF-8 \
  --chdir /workspace \
  "/opt/opencode/$OPENCODE_NAME" "${sandbox_args[@]}" < /dev/null
status=$?
set -e
exit "$status"
