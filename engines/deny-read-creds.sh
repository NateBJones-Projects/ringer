# Shared credential read deny-list for the ringer Seatbelt wrappers.
#
# Adapted from ringer PR #15 (mjskinner82, "opencode sandbox: deny reads of
# credential paths"), generalised so opencode-, claude-, and gemini-sandboxed.sh
# all share one source of truth.
#
# The wrappers' base profile is `(allow default)` plus write confinement, so a
# worker (running a model of the operator's choosing, possibly an untrusted free
# one from the catalog) can otherwise READ ~/.ssh, ~/.aws, ~/.codex, ~/.claude,
# gh/gcloud tokens, and the Keychain, with the network open to exfiltrate them.
# Workers never legitimately need those paths.
#
# KEY FINDING (verified on macOS): Seatbelt matches the path as PRESENTED, not
# the symlink-resolved target. Denying only realpath(p) leaves the symlinked
# spelling (the one real accesses use) readable. So every deny covers BOTH the
# path as written and its resolved target, each as subpath (dirs) and literal
# (files).
#
# Usage from a wrapper, after PROFILE is written and before sandbox-exec:
#   source "$(dirname "$0")/deny-read-creds.sh"
#   build_deny_read_args        # sets array DENY_ARGS and appends to $PROFILE
#   ... /usr/bin/sandbox-exec ... "${DENY_ARGS[@]}" -f "$PROFILE" ...
#
# IMPORTANT: pass the ENGINE'S OWN credential paths as arguments so they are NOT
# denied; the worker must read its own auth to function. The claude wrapper keeps
# ~/.claude readable, the gemini wrapper keeps ~/.gemini, etc. A blanket deny of
# every store would break whichever engine owns one:
#   build_deny_read_args "$HOME/.claude" "$HOME/.claude.json"
#
# Extend per machine (work cloud drives, extra credential stores) without editing
# this file:
#   - one path per line in ~/.config/ringer/ringer-deny-read.txt ('#' comments,
#     leading '~' expanded), or
#   - RINGER_DENY_READ, colon-separated.

# Populates the global DENY_ARGS array with `-D` parameters and appends a
# `(deny file-read* ...)` block to the file named by $PROFILE. No-op-safe:
# missing paths are skipped, duplicates de-duplicated, and if nothing exists the
# profile is left unchanged.
build_deny_read_args() {
  # Any argument is a path to KEEP readable (the engine's own credential store),
  # matched against both the candidate as written and its resolved target.
  local keep=("$@") k
  _is_kept() {
    # "${keep[@]}" on an empty array trips `set -u` on bash 3.2 (macOS default),
    # so guard on element count before expanding.
    [ "${#keep[@]}" -eq 0 ] && return 1
    for k in "${keep[@]}"; do [ "$1" = "$k" ] && return 0; done
    return 1
  }

  local candidates=(
    "$HOME/.ssh"
    "$HOME/.aws"
    "$HOME/.gnupg"
    "$HOME/.secrets"
    "$HOME/.netrc"
    "$HOME/.npmrc"
    "$HOME/.config/gh"
    "$HOME/.config/gcloud"
    "$HOME/.codex"
    "$HOME/.claude"
    "$HOME/.gemini"
    "$HOME/.grok"
    "$HOME/.config/ringer"
    "$HOME/Library/Keychains"
  )

  local deny_file="${RINGER_DENY_READ_FILE:-$HOME/.config/ringer/ringer-deny-read.txt}"
  if [ -f "$deny_file" ]; then
    local line
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in ''|'#'*) continue ;; esac
      candidates+=("${line/#\~/$HOME}")
    done < "$deny_file"
  fi
  if [ -n "${RINGER_DENY_READ:-}" ]; then
    local saved_ifs="$IFS" extra
    IFS=':'
    for extra in $RINGER_DENY_READ; do
      [ -n "$extra" ] && candidates+=("${extra/#\~/$HOME}")
    done
    IFS="$saved_ifs"
  fi

  DENY_ARGS=()
  local rules="" idx=0 seen="|" p real

  _add_one() {
    case "$seen" in *"|$1|"*) return 0 ;; esac
    seen="${seen}$1|"
    DENY_ARGS+=( -D "DENY_READ_${idx}=$1" )
    rules="${rules}  (subpath (param \"DENY_READ_${idx}\"))
  (literal (param \"DENY_READ_${idx}\"))
"
    idx=$((idx + 1))
  }

  for p in "${candidates[@]}"; do
    [ -e "$p" ] || continue
    _is_kept "$p" && continue
    real="$(/bin/realpath "$p" 2>/dev/null || true)"
    { [ -n "$real" ] && _is_kept "$real"; } && continue
    _add_one "$p"
    [ -n "$real" ] && [ "$real" != "$p" ] && _add_one "$real"
  done

  if [ -n "$rules" ]; then
    {
      printf '(deny file-read*\n'
      printf '%s' "$rules"
      printf ')\n'
    } >> "$PROFILE"
  fi
}
