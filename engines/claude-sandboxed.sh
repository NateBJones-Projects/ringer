#!/bin/bash
# Ringer engine wrapper: run Claude Code under a macOS Seatbelt sandbox.
#
# Claude Code has no OS-level sandbox of its own. Its --permission-mode
# bypassPermissions (required for headless runs that write files) disables ALL
# of its interactive approval prompts; its own --help says the equivalent
# --dangerously-skip-permissions is "recommended only for sandboxes". This
# wrapper supplies the real containment: full network and reads, writes confined
# to the task dir, a per-run scratch dir, and Claude's own state.
#
# NOTE ON CONFIG DIR: Claude's credentials live inside $HOME/.claude, and
# pointing CLAUDE_CONFIG_DIR elsewhere yields "Not logged in". So the worker
# must be allowed to write $HOME/.claude, and it therefore inherits whatever
# global CLAUDE.md, hooks, and skills live there. That is a real cost, in tokens
# and in blast radius. Route heavy batches to a cheaper, dumber lane.
#
# Usage (as a ringer engine bin):
#   claude-sandboxed.sh <taskdir> [--no-sandbox] <claude args...>
#
# macOS only (sandbox-exec); on other platforms only --no-sandbox mode works.
set -euo pipefail

TASKDIR="${1:?usage: claude-sandboxed.sh <taskdir> [--no-sandbox] <args...>}"; shift
SANDBOX=1
if [ "${1:-}" = "--no-sandbox" ]; then SANDBOX=0; shift; fi

if ! CLAUDE_BIN="$(command -v claude)" || [ -z "$CLAUDE_BIN" ]; then
  echo "claude-sandboxed.sh: claude not found on PATH" >&2
  exit 127
fi

if [ "$SANDBOX" = "0" ]; then
  exec "$CLAUDE_BIN" "$@" < /dev/null
fi

if [ ! -x /usr/bin/sandbox-exec ]; then
  echo "claude-sandboxed.sh: /usr/bin/sandbox-exec not available (macOS only)." >&2
  exit 1
fi

TASKDIR_REAL="$(cd "$TASKDIR" && pwd -P)"

# Per-run scratch root, wired as TMPDIR so we never open all of /private/tmp.
SCRATCH="$(cd "$(mktemp -d -t ringer-claude-scratch)" && pwd -P)"
PROFILE="$(mktemp -t ringer-claude-prof)"
cleanup() { rm -rf "$SCRATCH" "$PROFILE"; }
trap cleanup EXIT

# Paths reach the profile as sandbox-exec -D parameters, not string
# interpolation, so a task dir containing quotes or parens cannot inject rules.
cat > "$PROFILE" <<'SBEOF'
(version 1)
(allow default)
(deny file-write*)
(allow file-write*
  (subpath (param "TASKDIR"))
  (subpath (param "SCRATCH"))
  (subpath (param "CLAUDE_STATE")))
; Claude rewrites $HOME/.claude.json atomically, which needs the file itself
; plus sibling temp files in $HOME. Scope to the literal names, not all of $HOME.
(allow file-write*
  (literal (param "CLAUDE_JSON"))
  (regex (string-append "^" (param "HOMEDIR") "/\\.claude\\.json\\.")))
(allow file-write-data
  (literal "/dev/null")
  (literal "/dev/dtracehelper")
  (literal "/dev/tty")
  (literal "/dev/stdout")
  (literal "/dev/stderr"))
SBEOF

export TMPDIR="$SCRATCH"

# Deny credential reads. The worker keeps readable only what Claude Code needs to
# authenticate: ~/.claude, ~/.claude.json, and ~/Library/Keychains (Claude stores
# its OAuth token in the macOS login Keychain; verified: denying it yields "Not
# logged in"). Every OTHER credential store is denied, so a Claude worker cannot
# read ~/.ssh, ~/.codex, ~/.gemini, or gh/gcloud tokens.
#
# TRADE-OFF: keeping the Keychain readable means a malicious prompt to THIS worker
# could read other Keychain items. Accepted because this lane runs Claude Code, a
# first-party CLI on the operator's own account, not an untrusted catalog model.
# The opencode lane, where arbitrary OpenRouter models run, denies the Keychain.
# shellcheck source=deny-read-creds.sh
source "$(dirname "$0")/deny-read-creds.sh"
build_deny_read_args "$HOME/.claude" "$HOME/.claude.json" "$HOME/Library/Keychains"

set +e
/usr/bin/sandbox-exec \
  -D "TASKDIR=$TASKDIR_REAL" \
  -D "SCRATCH=$SCRATCH" \
  -D "CLAUDE_STATE=$HOME/.claude" \
  -D "CLAUDE_JSON=$HOME/.claude.json" \
  -D "HOMEDIR=$HOME" \
  "${DENY_ARGS[@]}" \
  -f "$PROFILE" "$CLAUDE_BIN" "$@" < /dev/null
status=$?
set -e
exit "$status"
