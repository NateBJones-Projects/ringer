#!/usr/bin/env bash
# Sync the shared dashboard + hud bridge into dist/ before every Tauri build.
# Self-locating: immune to whatever cwd the Tauri CLI uses.
set -euo pipefail
HUD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(dirname "$HUD_DIR")"
echo "sync-dist: cwd=$(pwd) hud=$HUD_DIR" >> /tmp/ringside-sync.log
mkdir -p "$HUD_DIR/dist"
cp "$REPO_DIR/dashboard/ringside.html" "$HUD_DIR/dist/index.html"
cp "$REPO_DIR/dashboard/ringside.css" "$HUD_DIR/dist/ringside.css"
cp "$REPO_DIR/dashboard/ringside.js" "$HUD_DIR/dist/ringside.js"
mkdir -p "$HUD_DIR/dist/assets"
cp "$REPO_DIR/dashboard/assets/ringside-mark.svg" "$HUD_DIR/dist/assets/ringside-mark.svg"
cp "$REPO_DIR/dashboard/assets/ringside-live.svg" "$HUD_DIR/dist/assets/ringside-live.svg"
cp "$REPO_DIR/dashboard/assets/ringside-attention.svg" "$HUD_DIR/dist/assets/ringside-attention.svg"
cp "$HUD_DIR/frontend/hud.js" "$HUD_DIR/dist/hud.js"
