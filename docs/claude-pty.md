# Claude Code as a subscription worker lane (PTY) — design & status

Ringer's worker lanes are all fire-and-forget subprocesses with **stdin closed**.
Claude Code can't join that way without giving up its subscription subsidy:
interactive `claude` bills as `cc_entrypoint=cli` (subsidized by a Max/Pro plan),
while `claude -p`/`--print` — the only mode that fits the closed-stdin model —
loses that subsidy. Subscription billing requires the interactive TUI, which
requires a real terminal. So a Claude lane needs a **PTY execution path**, not just
a config block.

## Status

The pattern and its reference implementation are spun out to a standalone repo:

**→ [`will-sargent-dbtlabs/claude-pty`](https://github.com/will-sargent-dbtlabs/claude-pty)**

Executed-verified there (via Ringer probes):

- A real `claude` turn driven through a `pty.fork()` PTY, authenticating on the
  **machine subscription** with `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` scrubbed
  from the child — no API key present, so it used the subscription session.
- The PTY plumbing in isolation (spawn, env scrub, bracketed-paste injection,
  filesystem-sentinel completion) against a stand-in CLI.
- `pty.fork()` works under the codex `workspace-write` sandbox; only network forces
  `full_access`.

Prototype (proof-pending): two-way, multi-turn sessions with `--resume`.

## How it would land in ringer

A `claude-pty` engine would be a new **execution path**, opted into by a single
engine field, reusing everything else:

1. Add `pty: bool = False` to `EngineConfig` beside `args_template` / `sandbox_args`
   / `full_access_args`; parse it in `load_engines`.
2. Branch in `RingerRunner._run_worker`: when `engine.pty`, open a PTY and spawn the
   worker with the slave as std streams, reading the master into the **same** log /
   rolling-capture path used today. Reuse the existing process-group timeout/kill.
3. Completion via a Claude Code `Stop` hook (injected through a temp `--settings`
   file) that writes a sentinel the runner polls for — ringer already writes
   Claude-hook-shaped settings in `merge_ringer_hook`. No HTTP server, no output
   parsing.

The full extension map (with `ringer.py` line citations) and the jinn teardown it
draws on live in the external repo under `docs/`.

## Invariant reconciliation

Ringer's four baked-in invariants survive:

- **stdin closed (`/dev/null`)** — the PTY master *replaces* `/dev/null` as the
  runner-owned stdin for this one engine class (a deliberate, documented
  exception); intent preserved (never wired to the user's terminal, sentinel-driven
  completion, same timeout + process-group kill).
- **sandbox explicit** — unchanged; still from `sandbox_args`/`full_access_args`.
- **verification executes the artifact** — unchanged, and it's *why* ringer needs
  none of jinn's output-extraction: the check reads the files the worker wrote.
- **logs carry raw worker output** — the raw PTY stream (ANSI and all) is the log.

## Gotchas (from the live proofs)

- Interactive `claude` never self-exits — never wait on child exit for completion.
- `--dangerously-skip-permissions` does **not** suppress every in-TUI confirmation;
  a headless driver must answer prompts or the turn stalls to timeout.
- First-run trust/onboarding dialogs hang a headless PTY — seed and restore the
  `~/.claude.json` flags.

See the external repo's `docs/findings.md` for the full account.
