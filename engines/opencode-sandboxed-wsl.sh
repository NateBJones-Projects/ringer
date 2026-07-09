#!/bin/bash
# Ringer engine wrapper: bridge from Windows-side ringer.py into the Linux
# bwrap sandbox (opencode-sandboxed-linux.sh) via WSL.
#
# Wire it in config.toml as:
#   bin = "wsl.exe"
#   args_template = ["-d", "Ubuntu", "-e", "<this script's /mnt path>",
#                    "{taskdir}", "{access_args}", ...]
#
# `wsl -e` execs this script directly (no shell), so multi-line spec text
# arrives intact as single argv elements. Any argument that is a Windows
# path (C:\..., D:/...) is translated to its /mnt equivalent before handing
# off to the Linux wrapper in this directory; everything else — including
# the spec text — passes through untouched.
set -euo pipefail

# `wsl -e` skips login shells, so user-local bin dirs (bwrap, opencode)
# are not on PATH the way they are in an interactive session.
export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

args=()
for a in "$@"; do
  if [[ "$a" =~ ^[A-Za-z]:[\\/] ]]; then
    a="$(wslpath -u "$a")"
  fi
  args+=("$a")
done

exec "$HERE/opencode-sandboxed-linux.sh" "${args[@]}"
