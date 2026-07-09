# Ringer × Meridian — Your Next Steps
*Written 2026-07-08. Everything not listed under "You do" is already done — do not redo it.*

---

## ✅ Already done (by Claude — nothing for you here)

- 4 swarm fixes reviewed, tested (870/870 green), committed, and **merged** into `mcp-local-rag`'s working branch
- `dist/` **rebuilt** — this is the file all six Meridian RAG servers load
- **Smoke-tested end-to-end** on the new build (ingest → status → semantic query: all pass)
- **Pushed** to your fork (`usjoh/mcp-local-rag`) — push-hook checks (biome + build) were run manually because `pnpm` isn't visible to non-interactive shells
- Ringer harness hardened (spawn jitter, worktree gitignore fix, lessons in `docs/MODEL-NOTES.md`)

---

## 👉 You do: ONE command (copy-paste exactly)

```bash
cd ~/Projects/ringer && ./ringer.py install-agent
```

**What it does:** installs the Ringer orchestrator playbook into Claude Code, so from now on your agents reach for swarms on their own — you stop typing Ringer commands forever.

**Success looks like:** it prints the skill + hooks it installed. That's it.

---

## 👉 Meridian: you do NOTHING

New Meridian sessions automatically load the fixed RAG build. No restart, no migration, no config change.

*Optional 10-second sanity check:* in your **next** Meridian session, say:
> "Run a status check on the hub RAG corpus."
A normal status response = the new build is live.

---

## 🗣 Later, if you want (just say it to Claude — no commands, no typing)

| Say this | What happens |
|---|---|
| **"Run wave 2"** | Swarm fixes the remaining smaller findings (JSDOM leaks, detector abort, error-message hygiene) |
| **"Prepare the upstream PRs"** | The 4 fixes get offered back to the original `shinpr/mcp-local-rag` project |
| **"Check if codex is fixed"** | Test whether OpenAI shipped a release that macOS no longer deletes |
| **"Show me the scoreboard"** | Opens your model pass-rate page (routing evidence) |

---

## 🧭 Tiny cheat sheet (the only Ringer commands worth knowing)

```bash
./ringer.py hud      # reopen the Ringside dashboard (http://127.0.0.1:8700)
./ringer.py models   # model scoreboard in the terminal
```

Everything else: describe what you want in Claude Code and let it orchestrate.
