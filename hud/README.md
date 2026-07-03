# Ringside

The native mission-control HUD for [Ringer](../README.md). Tauri v2 — one codebase builds macOS, Windows, and Linux.

## Build

```bash
cargo install tauri-cli --locked   # once
cargo tauri build                  # bundle lands in target/release/bundle/
cargo tauri dev                    # live-reload dev mode
```

The frontend is synced from the repo's shared `dashboard/dashboard.html` plus `frontend/hud.js` by `scripts/sync-dist.sh`, which runs automatically before every build. Edit those sources, never `dist/`.

## Behavior

- Frameless, always-on-top, visible on all Spaces/desktops; drag anywhere that isn't a control.
- Watches the Ringer state dir (`~/.ringer/runs/`, or `state_dir` from `~/.config/ringer/config.toml`) and renders every swarm: live, finished, and died (orchestrator gone without finishing).
- Tray icon: show/hide, version, quit. Closing the window hides it; the app stays in the tray.
- macOS-style close control top-left; ESC also hides.
