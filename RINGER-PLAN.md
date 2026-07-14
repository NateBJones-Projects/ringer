# Ringer — evaluation, placement decision & rollout plan

## Context
Adam wants to bring in **Ringer** (Nate Jones, `github.com/NateBJones-Projects/ringer`,
PolyForm-Shield 1.0.0 license) — a big new project. He asked for:
1. **Proof** I can actually read the guide (show the first paragraph of the start prompt). ✅ done.
2. A **full 4-way council** (wsl / lunk / ob / feeder) *before* I return with an explanation + plan.
3. A decided **placement**: fresh project, part of hermes, or one of the others — **with reasons**.

WSL (me) leads planning and gathers the reference materials. This file is the living plan;
it will be finalized only after the council reports.

## Proof of read (verbatim first paragraph of Ringer's start prompt)
The start prompt is an XML-structured prompt; its opening `<task>` block reads:
> Set up Ringer on this machine end to end: prove it works by running its built-in demo
> swarm, then walk me through the rest of the setup — cheap worker lanes, my first real
> manifest, the agent integration — pacing each step with me.

## What Ringer is (verified from the guide + repo README)
- **Single-file Python orchestrator** (`ringer.py`, Python 3.11+, stdlib only). Frontier model
  plans + reviews; **cheap worker CLIs implement in parallel**. Workers are never trusted —
  only the **executed check command** (exit 0 = PASS) decides pass/fail; a fail retries once
  with the failure context injected.
- **Engines/workers** (config `~/.config/ringer/config.toml`): Codex CLI (default), Grok-Build
  CLI (flat-rate), OpenCode + OpenRouter (per-token, any OpenRouter model, sandboxed). Custom
  engines via `[engines.x]` `bin` + `args_template`.
- **Manifest = JSON tasks**: `key`, `spec`, `check` (required) + `expect_files`, `engine`,
  `model`, `task_type`, `timeout_s`, `engine_args`, `verified`, `full_access`; run-level
  `worktrees` (isolated git worktree per task, removed on PASS).
- **Ringside** local dashboard (`http://127.0.0.1:8700`), nothing leaves the machine.
- **Eval loop**: every attempt logged to `~/.ringer/runs.jsonl` (spec, engine, duration, tokens,
  resolved model, task_type, retry, raw check output). `./ringer.py models` = per-(model,task_type)
  scoreboard with **`first_try_pass_rate`**; `--explore` = evidence-based routing tiers
  (proven/probation/untested, FREE-first from an OpenRouter catalog snapshot). Optional Postgres
  eval backend for cross-machine aggregation.
- **Agent integration**: `./ringer.py install-agent` installs a *user-level* Claude Code skill
  (orchestrator playbook) + two non-blocking nudge hooks into `~/.claude`.
- CLI: `demo`, `run <manifest> --max-parallel N`, `lint`, `hud`, `models`, `catalog --refresh`.
- **License**: PolyForm Shield 1.0.0 — free to use/modify/share incl. inside commercial work;
  cannot resell Ringer/Ringside (or a competitor) as a product.

## The council (4 voices) — 3 in, unanimous; 1 pending
- **WSL (me)** — lead. Verdict: fresh standalone repo; share-the-brain-via-API with Feeder.
- **OB-claude** ✅ VOTED (verified against live OB): **fresh standalone repo.** Reasons: (1) license
  clash — Ringer is PolyForm-Shield, OB is FSL-1.1-MIT with a strict contribution contract; hard no
  on vendoring into OB. (2) OB is the brain, Ringer is execution — coupling bloats OB. (3) not
  hermes-stack (welds a general tool to one private no-remote machine). (4) not feeder (different
  layer). Plus two constraints: **Ringer vs Open Engine = complementary, opt-in executor for the
  verifiable CODE-card SUBSET only, invoked BY Open Engine, never THE engine** — and it must inherit
  Open Engine's human safety gate: *"Ringer self-verifies correctness; it does not self-authorize
  consequence"* (no publish/deploy/delete/spend without a human). **Egress:** default local JSONL;
  if Postgres ever needed, use DIRECT libpq/psycopg (off the PostgREST meter) on a SEPARATE db —
  never the OB brain project's `/rest/v1` path (that was the 140% blowout pattern).
- **Lunk/hermes** ✅ (via hermes-stack Explore agent, evidence-cited): **awkward coupling, not a fit.**
  hermes-stack is the single-purpose Lunk Docker deployment (README/CLAUDE.md); its only two deploy
  sinks are the baked image (`patches/`) and the `~/.hermes` volume (`hermes-home/`) — neither can
  deploy a user-level tool that installs into `~/.claude`/`~/.config/ringer`. No standalone-tool
  precedent; no git remote; secret-heavy. Verdict: **peer repo, not a subdirectory.**
- **Feeder-claude** ✅ VOTED (grounded in live feeder arch): **fresh standalone repo**, complementary
  different layer. Two refinements (now CORE per Adam's directive): (1) **delete Ringer's internal
  router** — workers send `model:auto/<task_type>`, `consumer:ringer` (keeps it OFF web-augment —
  code must never be silently grounded), loose `latency_ceiling` (batch → favour quality); Feeder
  owns ALL model choice. (2) Feed `first_try_pass_rate` back as `source='ringer'` realtime-quality,
  blended over the arena prior, Feeder stays zero-policy (same lane as OB's agent-write-quality→
  task_scores). **LOAD-BEARING PREREQUISITE:** Feeder does not currently bench a quota-exhausted
  model (`quota_exhausted_until` on 0/181 health rows) — a parallel Ringer swarm is the exact
  free-tier-burst load that exhausts daily quotas, so feeder-claude's quota-bench fix must land
  BEFORE we open a full parallel swarm through `/v1`. OB +1'd this as a prerequisite.

## Decisions LOCKED (Adam, 2026-07-14)
- Placement: **fresh standalone local repo `~/ringer`** (WSL scaffolds it; not GitHub for now).
- Organiser: **WSL-Claude stays fleet-PM**; Ringer-Claude owns the repo build + reports to the board.
- Sequencing: **feeder's t6 (quota-bench) and the Ringer build run in PARALLEL now** — Ringer at LOW
  parallelism until t6 lands, then lift to full swarm.

## Adam's directives (2026-07-14, in-session)
- **North star: Claude Code = the ORCHESTRATOR; Feeder = the SWARM.** The workers get their LLM from
  Feeder — this is the whole point, not an optional integration.
- **v1 INCLUDES the Feeder seam** (workers-through-Feeder + the pass-rate feedback loop), not a later phase.
- Council was to complete before finalizing — now done, 4/4 unanimous.

## Feeder overlap (Explore agent, evidence-cited from the feeder repo)
**Complementary, different layers — not duplicative.** Feeder = OpenAI-compatible routing PROXY
(`POST /v1/chat/completions` at `:3001`) picking the best free-tier model per CALL, quality in local
Postgres `task_scores` (`realtime_quality` EWMA). Ringer = whole-TASK executor with shell-check
verify + retry, scoreboard = `first_try_pass_rate`. Feeder is the call-routing layer; Ringer sits
ABOVE it. **Synergies (the prize):** (1) Ringer's worker calls route THROUGH Feeder (base_url
→ `:3001/v1`, `needs:["tools"]`, `consumer:"ringer"`) — Feeder owns keys/rotation/failover, Ringer
stops re-implementing them; (2) Ringer POSTs its shell-check pass-rate to Feeder's
`POST /api/model-perf/sample` — a GROUND-TRUTH quality signal, cleaner than a judge's opinion, that
improves routing for every fleet caller; (3) Ringer reads Feeder's MCP `list_usable_models(coding)`
to pick live/cheap workers. **Duplications to avoid:** don't let Ringer build its own key-vault /
rotation / free-tier failover, don't maintain two divergent catalogs, don't silo two scoreboards,
avoid the redundant OpenRouter-through-Feeder-back-to-OpenRouter double hop.

## DECISION (4/4 council-backed): fresh standalone repo; Claude orchestrates, Feeder is the swarm
**Ringer lives as its own standalone repo** (`~/ringer`, cloned from upstream), owned by none of the
three systems, integrating via API. Claude Code is the orchestrator (plan/review); **every worker's
LLM comes from Feeder.**

### Architecture
- **Worker harness = an agentic CLI (OpenCode)** whose MODEL BACKEND is Feeder. A worker reads a
  spec, edits files in its task dir, exits; its LLM calls go to `http://localhost:3001/v1` (unified
  key). OpenCode is the sandboxed harness; Feeder picks the free-tier model. (Codex CLI could serve
  the same role if simpler on this box; OpenCode is the documented universal lane.)
- **Ringer's internal router is bypassed/deleted** — manifests carry `model:"auto/<task_type>"`,
  `consumer:"ringer"`, loose latency ceiling; Feeder makes every model choice. One brain, one catalog.
- **Two-way quality feed** — a small sidecar parses `~/.ringer/runs.jsonl` and POSTs per-(model,
  task_type) `first_try_pass_rate` to Feeder `POST /api/model-perf/sample`. **Verified body spec
  (feeder-claude, board 2026-07-14 11:25): the attribution field is `judge`, NOT `source` (no
  `source` param exists; it folds internally as `source='realtime_quality'`):**
  `{ model_id, task_class?, quality_score: 0..1, judge: "ringer" }` — samples key on CANONICAL
  model + task_type (a supplier row with no canonical link can't record a sample). Ground-truth
  signal, blended over the arena prior (0.6/0.4); improves routing for the whole fleet. (Ringer has
  no native Feeder awareness → this sidecar is ours to write.)
- **install-agent** installs the Ringer orchestrator skill + nudge hooks into `~/.claude` — this is
  what operationally makes Claude Code the orchestrator.
- **Eval store**: local `~/.ringer/runs.jsonl` only. No Supabase. (If cross-machine ever needed:
  direct libpq on a SEPARATE db, never OB's `/rest/v1`.)
- **Open Engine executor** (Ringer runs verifiable code-cards behind the human gate) = explicitly a
  LATER phase, not v1. "Self-verifies correctness, not consequence."
- **Reference materials**: keep in the standalone repo; the `hermes-stack/Ringer/` folder is optional
  and, per the hermes-stack finding, a slightly awkward tenant — revisit only if Adam wants it there.

### Rollout phases
- **Phase 0 — PREREQUISITE (feeder-claude's lane):** land the quota-bench health fix in Feeder before
  a full parallel swarm routes through `/v1`. Tracked on the coord board. Until it lands, v1 runs at
  LOW parallelism (`--max-parallel 1-2`).
- **Phase 1 — Scaffold:** create `~/ringer`; verify `python3 >=3.11`; clone upstream; copy
  `config.sample.toml` → `~/.config/ringer/config.toml`.
- **Phase 2 — The seam (load-bearing):** install OpenCode; configure it to use Feeder as a custom
  OpenAI-compatible provider (base_url `:3001/v1`, unified key, `model:auto`). VERIFY a single worker
  call shows up in Feeder's `requests` log tagged `consumer=ringer`. This is the make-or-break step.
  **Key retrieval (verified, feeder-claude 2026-07-14):** the unified key is NOT in Feeder's `.env`;
  it lives in Feeder's DB (settings table, `unified_api_key`) — fetch via `GET /api/settings/api-key`
  → `{apiKey}`, send as `Authorization: Bearer <key>`.
- **Phase 3 — Prove Ringer:** `./ringer.py demo` green + Ringside at `127.0.0.1:8700`. (If the demo's
  Codex default isn't available, point the demo engine at the Feeder-backed OpenCode worker.)
- **Phase 4 — First real manifest:** one small real task, run through the Feeder-backed swarm at low
  parallelism, check passes end-to-end.
- **Phase 5 — Quality feedback:** the runs.jsonl → `/api/model-perf/sample` sidecar (body:
  `{model_id, task_class?, quality_score, judge:"ringer"}` — `judge`, not `source`); confirm a
  sample row with `judge='ringer'` lands in Feeder's `task_scores`.
- **Phase 6 — install-agent:** register the orchestrator skill + hooks in `~/.claude`.

### Bootstrap & coordination — how Ringer-Claude is born + joins the board (verified pattern)
Ringer gets its OWN dedicated Claude ("Ringer-Claude") = a Claude Code session with cwd=`~/ringer`,
same WSL `ajo` user, distinguished by working dir + `COORD_AGENT`. Proven by feeder-claude, which is
also a WSL session identified only by `COORD_AGENT=feeder-claude` in its repo `.claude/settings.json`
(`coord.js`: `process.env.COORD_AGENT || (win32?windows:wsl)`). No Supabase/networking — same machine,
same shared `/mnt/c/.../Open Brain/.claude/coordination/events.jsonl` board.
- `~/ringer/.claude/settings.json` → `{"env":{"COORD_AGENT":"ringer-claude"}}`. The user-level coord
  hooks (already in `~/.claude`, shared) then show it the board each turn as `@ringer-claude`.
- `~/ringer/CLAUDE.md` → identity, board protocol + peer-watch Monitor command (idle wake-ups), the
  north star (Claude orchestrates / Feeder is the swarm), the plan, fleet rules (verify-first, no
  stale docs, egress discipline).
- **Organiser model (recommended):** WSL-Claude stays fleet-PM (master plan, cross-system seams, Adam
  relationship); Ringer-Claude owns the ringer repo build/operation and reports to the board — same
  "each Claude owns its repo" model as OB/feeder. (Full handoff to Ringer is an alternative = Adam's call.)

### Bootstrap sequence (on approval)
1. WSL scaffolds `~/ringer`: clone upstream Ringer + `.claude/settings.json` (COORD_AGENT) + a
   Ringer `CLAUDE.md` + copy this plan in. (Touches nothing else; doesn't hammer feeder.)
2. WSL hands Adam a short start prompt.
3. Adam green-lights feeder's **t6** (quota-bench) → feeder ships it in parallel.
4. Adam opens Claude Code in `~/ringer`, pastes the start prompt → Ringer-Claude introduces itself on
   the board, starts peer-watch, executes Phases 1→4 (scaffold done → seam-verify → demo → first
   manifest at LOW parallelism).
5. feeder pings "quota-bench LIVE" → Ringer-Claude lifts the parallelism throttle → full swarm.

### Open technical unknowns to verify at execution (flagged, not blockers)
1. OpenCode (or Codex) accepting a **custom Feeder base_url with `model:auto`** and passing it
   through — the crux of "Feeder is the swarm." Verified in Phase 2 before anything else.
2. Which agentic worker CLI is installed/authed on this WSL box (OpenCode? Codex?) — probe in Phase 1.

## Verification (end-to-end)
- `python3 --version` ≥ 3.11; OpenCode installed + a live worker.
- A single worker call appears in Feeder's `requests` table with `consumer=ringer` (Phase 2).
- `./ringer.py demo` → green verdict table; Ringside opens at `127.0.0.1:8700`.
- A first real manifest's shell check passes end-to-end, workers served by Feeder.
- A sample row with `judge='ringer'` appears in Feeder's `task_scores` after a run (Phase 5).
- Full parallel swarm gated on Phase 0 (quota-bench fix) landing.
