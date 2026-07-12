#!/usr/bin/env bash
# Run ringer.py inside the worker container (Linux write-isolation lane).
#
# What the container can touch:
#   - this repo, mounted READ-ONLY at its real path
#   - ~/.ringer, the ONE writable volume (state, workdirs, artifacts, logs) —
#     mounted at the identical path so the host Ringside reads the same files
#   - a tmpfs HOME for opencode scratch, gone when the container exits
# Auth: OPENROUTER_API_KEY is passed through from the caller's environment
# (loaded by zshrc from keep/.secrets); nothing credential-shaped is on disk.
#
# Usage: docker/run.sh run ~/.ringer/manifests/foo.json --identity <who> [...]
#        (any ringer.py arguments work; manifest paths must live under ~/.ringer
#         or the repo, since those are the only mounts)
# Extra read-only mounts (e.g. a repo the workers must READ but never write):
#   RINGER_MOUNTS_RO="/path/one /path/two" docker/run.sh run ...
# Each path is mounted read-only at its identical in-container path.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${RINGER_IMAGE:-ringer-worker}"

: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY not set — eval \"\$(manifest age decrypt ~/repos/scottidler/keep/.secrets/openrouter-api-key.age)\"}"

mkdir -p "$HOME/.ringer"

EXTRA_MOUNTS=()
for p in ${RINGER_MOUNTS_RO:-}; do
  EXTRA_MOUNTS+=(-v "$p:$p:ro")
done

# Config rides the read-only repo mount and is passed via --config; nothing is
# bind-mounted under the tmpfs HOME (a bind there makes docker pre-create
# root-owned parent dirs, which breaks opencode's own ~/.config/opencode mkdir).
exec docker run --rm \
  -e OPENROUTER_API_KEY \
  -e HOME="$HOME" \
  --user "$(id -u):$(id -g)" \
  --tmpfs "$HOME:exec,uid=$(id -u),gid=$(id -g),size=512m" \
  -v "$REPO_DIR:$REPO_DIR:ro" \
  -v "$HOME/.ringer:$HOME/.ringer" \
  "${EXTRA_MOUNTS[@]}" \
  -w "$REPO_DIR" \
  "$IMAGE" \
  --config "$REPO_DIR/docker/config.toml" \
  "$@"
