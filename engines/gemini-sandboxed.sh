#!/bin/bash
# Ringer engine wrapper: run the Gemini CLI under a macOS Seatbelt sandbox.
#
# Gemini CLI has a -s/--sandbox flag of its own, but it delegates to Docker or
# Podman unless GEMINI_SANDBOX=sandbox-exec, and its behaviour is not verified
# here. We supply our own containment, matching the opencode and claude wrappers:
# full network and reads, writes confined to the task dir, a per-run scratch dir,
# and Gemini's own state dir.
#
# Two Gemini-specific quirks this wrapper handles:
#
#  1. FOLDER TRUST. Gemini 0.46 silently downgrades --approval-mode yolo to
#     "default" when the working directory is not a trusted folder, then exits
#     nonzero without doing the work. Ringer task dirs are freshly-created temp
#     directories and will never be trusted, so --skip-trust is mandatory and is
#     supplied by the engine's args_template, not here.
#
#  2. GEMINI_API_KEY. Auth is the API key exported by ~/.zshrc line 5, which is
#     valid (HTTP 200, 50 models). Until 2026-07-09 a 13-character placeholder
#     appended by Antigravity at line 104 shadowed it, so every call returned
#     API_KEY_INVALID; that line is now commented out. The key is INHERITED from
#     the environment, so ringer must be launched from a shell that sourced the
#     fixed .zshrc. GOOGLE_API_KEY is stripped because, when set, it can take
#     precedence and reintroduce exactly this class of shadowing bug.
#
# Usage (as a ringer engine bin):
#   gemini-sandboxed.sh <taskdir> [--no-sandbox] <gemini args...>
set -euo pipefail

TASKDIR="${1:?usage: gemini-sandboxed.sh <taskdir> [--no-sandbox] <args...>}"; shift
SANDBOX=1
if [ "${1:-}" = "--no-sandbox" ]; then SANDBOX=0; shift; fi

if ! GEMINI_BIN="$(command -v gemini)" || [ -z "$GEMINI_BIN" ]; then
  echo "gemini-sandboxed.sh: gemini not found on PATH" >&2
  exit 127
fi

if [ "$SANDBOX" = "0" ]; then
  exec env -u GOOGLE_API_KEY "$GEMINI_BIN" "$@" < /dev/null
fi

if [ ! -x /usr/bin/sandbox-exec ]; then
  echo "gemini-sandboxed.sh: /usr/bin/sandbox-exec not available (macOS only)." >&2
  exit 1
fi

TASKDIR_REAL="$(cd "$TASKDIR" && pwd -P)"
SCRATCH="$(cd "$(mktemp -d -t ringer-gemini-scratch)" && pwd -P)"
PROFILE="$(mktemp -t ringer-gemini-prof)"
cleanup() { rm -rf "$SCRATCH" "$PROFILE"; }
trap cleanup EXIT

# Paths reach the profile as sandbox-exec -D parameters, never string
# interpolation, so a task dir containing quotes or parens cannot inject rules.
cat > "$PROFILE" <<'SBEOF'
(version 1)
(allow default)
(deny file-write*)
(allow file-write*
  (subpath (param "TASKDIR"))
  (subpath (param "SCRATCH"))
  (subpath (param "GEMINI_STATE")))
(allow file-write-data
  (literal "/dev/null")
  (literal "/dev/dtracehelper")
  (literal "/dev/tty")
  (literal "/dev/stdout")
  (literal "/dev/stderr"))
SBEOF

export TMPDIR="$SCRATCH"
export XDG_CACHE_HOME="$SCRATCH/cache"
mkdir -p "$XDG_CACHE_HOME"

# Deny credential reads. The worker keeps ~/.gemini readable (its OAuth lives
# there); every OTHER credential store is denied, so a Gemini worker cannot read
# ~/.ssh, ~/.codex, ~/.claude, gh tokens, or the Keychain.
# shellcheck source=deny-read-creds.sh
source "$(dirname "$0")/deny-read-creds.sh"
build_deny_read_args "$HOME/.gemini"

set +e
/usr/bin/sandbox-exec \
  -D "TASKDIR=$TASKDIR_REAL" \
  -D "SCRATCH=$SCRATCH" \
  -D "GEMINI_STATE=$HOME/.gemini" \
  "${DENY_ARGS[@]}" \
  -f "$PROFILE" \
  env -u GOOGLE_API_KEY "$GEMINI_BIN" "$@" < /dev/null
status=$?
set -e
exit "$status"
