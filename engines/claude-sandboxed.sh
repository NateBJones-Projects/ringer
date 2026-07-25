#!/bin/bash
# Ringer engine wrapper: run the `claude` CLI (Claude Code) under a macOS
# Seatbelt sandbox as a headless Ringer worker.
#
# claude has no OS-level sandbox of its own (headless -p mode needs
# --dangerously-skip-permissions to run without interactive approval prompts),
# so this wrapper supplies the real containment: full network and reads, writes
# confined to the task dir, a per-run scratch/cache dir, and claude's own
# state/config locations (~/.claude, ~/.claude.json, and its small
# ~/.cache/claude staging dir).
#
# Usage (as a ringer engine bin):
#   claude-sandboxed.sh <taskdir> [--no-sandbox] <claude args...>
#
# The first argument is the task directory (pass "{taskdir}" first in
# args_template). "--no-sandbox" as the second argument skips Seatbelt entirely
# — wire it as the engine's full_access_args so ringer's allow_full_access gate
# still applies. macOS only (sandbox-exec); on other platforms only
# --no-sandbox mode works.
#
# ---------------------------------------------------------------------------
# Inference endpoint: two modes, both supported, nothing machine-specific here
# ---------------------------------------------------------------------------
#
# 1. Default (no env file): plain `claude` auth applies — your normal Anthropic
#    API key / login, exactly as an interactive `claude` run would resolve it.
#
# 2. Alternative Anthropic-compatible endpoint: if
#    ~/.config/ringer/claude-engine.env exists it is sourced (before the sandbox
#    is entered, so no profile rule is needed to read it) and its exports are
#    inherited by claude. Anything that speaks the Anthropic /v1/messages API
#    works — Ollama, LM Studio, LiteLLM, a self-hosted gateway. Typical file:
#
#      # ~/.config/ringer/claude-engine.env
#      export ANTHROPIC_BASE_URL="http://your-ollama-host:11434"
#      export ANTHROPIC_AUTH_TOKEN="local"   # any non-empty value; local
#                                            # servers usually ignore it
#      export ANTHROPIC_API_KEY=             # empty: no silent fallback to a
#                                            # real Anthropic key or to
#                                            # keychain/OAuth auth, which is
#                                            # unreachable non-interactively
#
#    Keep that file out of version control — it is machine config, not repo
#    code. Endpoint/model choice belongs there and in your config.toml's
#    [engines.claude] block, never in this script. Set
#    RINGER_CLAUDE_ENGINE_ENV to source a different path (handy for testing
#    both modes without moving the real file).
#
# ---------------------------------------------------------------------------
# Why the engine's args_template should carry --bare and --strict-mcp-config
# ---------------------------------------------------------------------------
#
# Both are passed by the caller (see the [engines.claude] sample block in
# config.sample.toml), not by this wrapper, so a fully-configured Claude Code
# worker is still possible when that is what you want. They matter most for
# small local models: headless claude otherwise inherits the operator's whole
# interactive environment — MCP server rosters, plugin hooks, auto-memory, and
# the always-loaded global ~/.claude/CLAUDE.md — and a small model answers that
# injected context instead of the task spec. --bare skips hooks, plugin sync,
# auto-memory, and CLAUDE.md auto-discovery; --strict-mcp-config (with no
# --mcp-config supplied) loads zero MCP servers. See the entry in
# docs/MODEL-NOTES.md for the probe evidence.
set -euo pipefail

TASKDIR="${1:?usage: claude-sandboxed.sh <taskdir> [--no-sandbox] <args...>}"; shift
SANDBOX=1
if [ "${1:-}" = "--no-sandbox" ]; then SANDBOX=0; shift; fi

# Resolve claude without tripping `set -e` (command -v returns nonzero when absent).
if ! CLAUDE_BIN="$(command -v claude)" || [ -z "$CLAUDE_BIN" ]; then
  echo "claude-sandboxed.sh: claude not found on PATH" >&2
  exit 127
fi

# Optional machine-local endpoint config. Absent by default; when absent, plain
# `claude` auth applies. See the header for the file's shape.
CLAUDE_ENGINE_ENV="${RINGER_CLAUDE_ENGINE_ENV:-${XDG_CONFIG_HOME:-$HOME/.config}/ringer/claude-engine.env}"
if [ -f "$CLAUDE_ENGINE_ENV" ]; then
  # shellcheck source=/dev/null
  . "$CLAUDE_ENGINE_ENV"
fi

if [ "$SANDBOX" = "0" ]; then
  exec "$CLAUDE_BIN" "$@" < /dev/null
fi

if [ ! -x /usr/bin/sandbox-exec ]; then
  echo "claude-sandboxed.sh: /usr/bin/sandbox-exec not available (macOS only)." >&2
  echo "Use the engine's full-access mode (--no-sandbox) or add your own sandbox." >&2
  exit 1
fi

TASKDIR_REAL="$(cd "$TASKDIR" && pwd -P)"

# Per-run scratch root — becomes both TMPDIR and XDG_CACHE_HOME for claude, so
# we never have to open all of /private/tmp or ~/.cache to the sandboxed agent.
# Resolve to the real path (/var/folders symlinks to /private/var/folders);
# Seatbelt subpath matching needs the canonical path or writes EPERM-crash.
SCRATCH="$(cd "$(mktemp -d -t ringer-claude-scratch)" && pwd -P)"
PROFILE="$(mktemp -t ringer-claude-prof)"
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
  (subpath (param "CLAUDE_HOME"))
  (subpath (param "CLAUDE_CACHE"))
  (literal (param "CLAUDE_JSON")))
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
  -D "CLAUDE_HOME=$HOME/.claude" \
  -D "CLAUDE_CACHE=$HOME/.cache/claude" \
  -D "CLAUDE_JSON=$HOME/.claude.json" \
  -f "$PROFILE" "$CLAUDE_BIN" "$@" < /dev/null
status=$?
set -e
exit "$status"
