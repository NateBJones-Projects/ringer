# Ringside

The native mission-control HUD for [Ringer](../README.md). Tauri v2 — one codebase builds macOS, Windows, and Linux.

## Build

```bash
cargo install tauri-cli --locked   # once
cargo tauri build                  # bundle lands in target/release/bundle/
cargo tauri dev                    # live-reload dev mode
```

The frontend is shared with the browser: `dashboard/ringside.html`, `dashboard/ringside.css`, `dashboard/ringside.js`, and `dashboard/assets/`, plus the native transport in `frontend/hud.js`. Both `scripts/sync-dist.sh` and `build.rs` copy these sources before embedding them. Edit the sources, never `dist/`. Native Models and Open folder use the local browser HUD; keep `ringer.py hud` running for those actions.

## Behavior

- Frameless, always-on-top, visible on all Spaces/desktops; drag anywhere that isn't a control.
- Watches the Ringer state dir (`~/.ringer/runs/`, or `state_dir` from `~/.config/ringer/config.toml`) and renders every swarm: live, finished, and died (orchestrator gone without finishing).
- Tray icon: show/hide, version, quit. Closing the window hides it; the app stays in the tray.
- macOS-style close control top-left; ESC also hides.
