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
# RINGER_EXTRA_WRITABLE (optional, colon-separated absolute dirs) widens the
# writable set. Some toolchains cannot build with only the task dir writable:
# the .NET SDK writes NuGet caches, ~/.dotnet and MSBuild node state outside the
# repo, and when those writes are denied MSBuild does not error — it HANGS with
# no output, which a worker cannot diagnose. Grant the minimum, e.g.
#   RINGER_EXTRA_WRITABLE="$HOME/.nuget:$HOME/.dotnet"
# Entries are passed as -D params like every other path, never interpolated into
# the profile text, so the rule-injection guarantee below still holds.
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

# Per-run scratch root — becomes both TMPDIR and XDG_CACHE_HOME for OpenCode, so
# we never have to open all of /private/tmp or ~/.cache to the sandboxed agent.
# Resolve to the real path (/var/folders symlinks to /private/var/folders);
# Seatbelt subpath matching needs the canonical path or writes EPERM-crash.
SCRATCH="$(cd "$(mktemp -d -t ringer-opencode-scratch)" && pwd -P)"
PROFILE="$(mktemp -t ringer-opencode-prof)"
cleanup() { rm -rf "$SCRATCH" "$PROFILE"; }
trap cleanup EXIT

# Optional extra writable roots (see RINGER_EXTRA_WRITABLE in the header). Each
# entry becomes its own -D param + rule; the loop only ever emits the fixed text
# (subpath (param "EXTRA_<n>")), so a path can still never inject a rule.
EXTRA_RULES=""
EXTRA_DEFS=()
if [ -n "${RINGER_EXTRA_WRITABLE:-}" ]; then
  extra_i=0
  while IFS= read -r extra_raw; do
    [ -n "$extra_raw" ] || continue
    if [ ! -d "$extra_raw" ]; then
      echo "opencode-sandboxed.sh: RINGER_EXTRA_WRITABLE entry is not a directory: $extra_raw" >&2
      exit 1
    fi
    # Canonicalise: Seatbelt subpath matching needs the real path (/var/folders
    # is a symlink to /private/var/folders) or writes EPERM-crash at runtime.
    extra_real="$(cd "$extra_raw" && pwd -P)"
    EXTRA_RULES="$EXTRA_RULES
  (subpath (param \"EXTRA_$extra_i\"))"
    EXTRA_DEFS+=(-D "EXTRA_$extra_i=$extra_real")
    extra_i=$((extra_i + 1))
  done <<EOF
$(printf '%s' "$RINGER_EXTRA_WRITABLE" | tr ':' '\n')
EOF
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
  (subpath (param "OC_CONFIG"))
SBEOF
printf '%s\n  )\n' "$EXTRA_RULES" >> "$PROFILE"
cat >> "$PROFILE" <<'SBEOF'
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
  ${EXTRA_DEFS[@]+"${EXTRA_DEFS[@]}"} \
  -f "$PROFILE" "$OPENCODE_BIN" "$@" < /dev/null
status=$?
set -e
exit "$status"
