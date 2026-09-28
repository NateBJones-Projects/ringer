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
#   opencode-sandboxed.sh <taskdir> [--no-sandbox] [--writable-root PATH]... <opencode args...>
#
# The first argument is the task directory (pass "{taskdir}" first in
# args_template). "--no-sandbox" as the second argument skips Seatbelt entirely
# — wire it as the engine's full_access_args so ringer's allow_full_access gate
# still applies. macOS only (sandbox-exec); on other platforms only
# --no-sandbox mode works.
#
# --writable-root PATH  (repeatable) opens an extra directory tree to the
# Seatbelt file-write allowlist. Use it to hand a worker the repository it must
# edit — e.g. engine_args = ["--writable-root", "{{REPO_PATH}}"]. Because ringer
# splices {engine_args} into the middle of the engine's args_template, this
# option is recognized anywhere after <taskdir> and stripped before OpenCode is
# invoked (in both sandbox and --no-sandbox mode); a bare "--" ends wrapper-option
# stripping and is itself consumed. This replaces the Codex-only config string
# -c sandbox_workspace_write.writable_roots=... that the OpenCode wrapper does
# NOT understand — custom engines must provide an equivalent mechanism or their
# workers hit EPERM on every repo edit.
set -euo pipefail

TASKDIR="${1:?usage: opencode-sandboxed.sh <taskdir> [--no-sandbox] [--writable-root PATH]... <args...>}"; shift
SANDBOX=1
if [ "${1:-}" = "--no-sandbox" ]; then SANDBOX=0; shift; fi

# Parse the remaining arguments: collect every --writable-root PATH (validated
# and canonicalized), stripping all wrapper options so OpenCode never sees them.
# A bare "--" terminates wrapper-option parsing and is consumed. Validation is
# mode-independent: a bad root fails even under --no-sandbox.
WR_ROOTS=()
OC_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --writable-root)
      if [ $# -lt 2 ]; then
        echo "opencode-sandboxed.sh: --writable-root requires a PATH argument" >&2
        exit 2
      fi
      root="$2"; shift 2
      if [ ! -d "$root" ]; then
        echo "opencode-sandboxed.sh: --writable-root not a directory: $root" >&2
        exit 2
      fi
      WR_ROOTS+=( "$(cd "$root" && pwd -P)" )
      ;;
    --)
      shift; OC_ARGS+=( "$@" ); break ;;
    *)
      OC_ARGS+=( "$1" ); shift ;;
  esac
done

# Resolve opencode without tripping `set -e` (command -v returns nonzero when absent).
if ! OPENCODE_BIN="$(command -v opencode)" || [ -z "$OPENCODE_BIN" ]; then
  echo "opencode-sandboxed.sh: opencode not found on PATH" >&2
  exit 127
fi

if [ "$SANDBOX" = "0" ]; then
  exec "$OPENCODE_BIN" ${OC_ARGS[@]+"${OC_ARGS[@]}"} < /dev/null
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
# interpolation — a task dir or writable root containing quotes/parens/newlines
# can't inject rules. Writable roots are referenced only by parameter name
# (WR_ROOT_<n>); their paths travel exclusively through -D parameters.
{
  cat <<'SBEOF'
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
  i=0
  for _root in ${WR_ROOTS[@]+"${WR_ROOTS[@]}"}; do
    printf '(allow file-write* (subpath (param "WR_ROOT_%d")))\n' "$i"
    i=$((i + 1))
  done
} > "$PROFILE"

export TMPDIR="$SCRATCH"
export XDG_CACHE_HOME="$SCRATCH/cache"
mkdir -p "$XDG_CACHE_HOME"

# Assemble the -D parameter list (paths only — never inlined into the profile).
D_PARAMS=(
  -D "TASKDIR=$TASKDIR_REAL"
  -D "SCRATCH=$SCRATCH"
  -D "OC_SHARE=$HOME/.local/share/opencode"
  -D "OC_STATE=$HOME/.local/state/opencode"
  -D "OC_CONFIG=$HOME/.config/opencode"
)
i=0
for _root in ${WR_ROOTS[@]+"${WR_ROOTS[@]}"}; do
  D_PARAMS+=( -D "WR_ROOT_$i=$_root" )
  i=$((i + 1))
done

# Run as a child (not exec) so the EXIT trap fires and cleans up the profile +
# scratch dir even on the success path; propagate the child's exit status.
set +e
/usr/bin/sandbox-exec ${D_PARAMS[@]+"${D_PARAMS[@]}"} -f "$PROFILE" "$OPENCODE_BIN" ${OC_ARGS[@]+"${OC_ARGS[@]}"} < /dev/null
status=$?
set -e
exit "$status"
