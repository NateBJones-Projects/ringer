---
project: ringer-integration
status: proposed
level: 2
created: 2026-07-09
owner: Sextant (Agentic OS) implements; this spec is owned by the Ringer repo
source_repo: /Users/usjoh/Projects/ringer
target_repo: /Users/usjoh/Projects/sextant
---

# Ringer → Sextant (Agentic OS) integration plan

> **How to use this document.** Open a Claude Code session in `~/Projects/sextant`
> and say: *"Read `~/Projects/ringer/docs/integrations/sextant-agentic-os.md` and
> implement it."* It is written to be evaluated and executed by Sextant inside
> Agentic OS's own conventions (skills, registry, reconciliation, branching).
> Ringer's repo owns this spec; **Sextant owns the implementation** and may adapt
> naming to its house style. Nothing here should be copied verbatim from
> Meridian — see §"Why not copy Meridian".

---

## TL;DR

Make Ringer a **first-class, routable capability inside Sextant** — so Agentic
OS's Task Routing, Skill Registry, and reconciliation know Ringer exists and when
to delegate to it — **without duplicating the global playbook**.

- **Phase 1 (do this):** add and register a thin `tool-ringer` skill.
- **Phase 2 (optional, later):** wire `cron/jobs` to invoke `ringer.py run` for
  scheduled swarm-shaped work.

**Acceptance for Phase 1:** a fresh Sextant session, handed a batch of ≥2
independent, executably-checkable items, routes to `tool-ringer` (instead of
"no skill matches") and the skill shows up in `bash scripts/list-skills.sh` and
`docs/skill-registry.md`.

---

## Background — what you need to know

### What Ringer is
Ringer is a **verified-swarm delegation CLI** (`~/Projects/ringer/ringer.py`). It
fans one batch of work out to N parallel cheap workers from a JSON manifest and
**trusts nothing but an executable check — exit 0 is the only PASS** ("the check
is the contract"). Each failing task is retried exactly once with the check's
real failure output injected into the retry prompt; every attempt is logged to a
machine-global JSONL eval log that feeds a per-`(model, task_type)` routing
scoreboard. The orchestrator (you) writes specs + checks and reviews survivors;
cheap sandboxed workers do the typing.

### Two layers are ALREADY in place globally (nothing to redo)
Both apply to **every** Claude Code project on this machine, Sextant included:

1. **Global skill** — `~/.claude/skills/ringer/SKILL.md` (name: `ringer`). The
   authoritative playbook: operating loop, swarm patterns (review/fix/focus-group/
   bakeoff/research-with-proof), the review ritual. It loads in every session.
2. **Two never-blocking nudge hooks** in `~/.claude/settings.json`
   (`hooks/ringer_nudge.py` on `PreToolUse:Bash` and `PostToolUse:Edit|Write`).

### The gap this plan closes
Agentic OS routes by **project** skills. From Sextant's `AGENTS.md` §Task
Routing: *"search `.claude/skills/` frontmatter for a matching skill … never
silently fall back to base knowledge when a skill exists."* That search only sees
Sextant's **own** `.claude/skills/`. So although the global Ringer skill is still
*available* (and the hooks still nudge), Sextant's **routing, Skill Registry,
reconciliation, and cron authoring are blind to it** — Ringer is not one of
Sextant's known capabilities. This plan registers it the Agentic OS way.

### Why not copy Meridian
Meridian closed the analogous gap with a `kos/` capability record
(`kos/capabilities/ringer.yaml` + `kos/tasks/ringer-delegation.md`). Those are
**Meridian-native** — they lean on Meridian's lifecycle graph, decision records
(CD-073/075), egress governance (PR-20260601), and MCP write-back tools. None of
that machinery exists in Sextant. The faithful analog in Agentic OS is a
**registered skill**, not a YAML capability file. Do not port the Meridian files.

---

## Phase 1 — the `tool-ringer` skill (primary deliverable)

### Step 1 — Create the skill file
Path: `.claude/skills/tool-ringer/SKILL.md`

Conventions to honor (from `AGENTS.md`):
- Category prefix `tool` = "Utility / Integration"; folder name = `tool-ringer`.
- Frontmatter `name` **must equal** the folder name exactly (`tool-ringer`).
- It's user-authored (not upstream), so it's a plain `SKILL.md` (no
  `SKILL.local.md` needed).

### Step 2 — Recommended SKILL.md content (ready to adapt)
The description below is deliberately trigger-heavy so Task Routing surfaces it.
The body stays **thin and points to the global playbook** rather than duplicating
it.

```
---
name: tool-ringer
description: >-
  Delegate a batch of independent, executably-checkable work to Ringer — the
  verified-swarm CLI (~/Projects/ringer/ringer.py) that fans a JSON manifest out
  to N cheap parallel workers and trusts nothing but an executable check (exit 0
  is the ONLY pass). TRIGGER — reach for this BEFORE acting whenever: you are
  about to run any model-driven script / probe / eval / persona-sim outside a
  live run; start an edit→test→edit loop or a batch of similar edits across
  files; do a "quick check" that spawns a model or a CLI agent; or review /
  diagnose failed worker output. A single such task is a one-task manifest —
  "small enough to just do myself" IS the trigger. SKIP for: reading or
  searching files, git operations, a one-file few-line ONE-SHOT edit (a second
  pass is a loop → trigger), prose written straight from your own context, or
  pure conversation.
---

# tool-ringer — verified-swarm delegation (Sextant pointer)

**This skill is a thin Sextant-side pointer.** The authoritative, machine-global
playbook is `~/.claude/skills/ringer/SKILL.md` — **load it before acting**. It
owns the operating loop, swarm patterns, and the post-run review ritual. This
file adds only how Ringer fits Sextant.

## The four rules that actually get broken
1. You review; workers type. Your lane is specs, checks, pattern choice, reading
   results — not implementation or babysitting retries.
2. A single task is a one-task manifest. Same verification, zero ceremony.
3. Beware the tiny-edit death spiral: the second pass on the same problem is a
   loop, and loops are manifests.
4. Runs are watched: put Ringside on screen FIRST — `python3
   ~/Projects/ringer/ringer.py hud` (http://127.0.0.1:8700). Never `open -a
   Ringside` (parked prototype).

## Before the first run of any job
1. `python3 ~/Projects/ringer/ringer.py hud` — Ringside on the human's screen first.
2. Route the model: `python3 ~/Projects/ringer/ringer.py models --task-type <type>`;
   present the top 2–3 options with a recommendation; ask once; honor the pick via
   per-task `engine` / `model`.
3. `python3 ~/Projects/ringer/ringer.py lint <manifest.json>` before `run`;
   treat findings as spec defects.

## Manifest task contract (per task)
`key` (task dir + label) · `spec` (self-contained prompt handed verbatim to a
stateless worker; pointer specs are lint-flagged) · `check` (any shell command,
exit 0 is the only PASS, 60s timeout, must print WHY it fails and verify content
not existence) · `expect_files` (non-empty precondition floor) · `engine`
(default codex) · `model` · `task_type` (scoreboard slice — code-fix, code-review,
docs, research, test-hardening, probe, bakeoff, …) · `timeout_s` (default 900) ·
`engine_args` · `verified` (one plain-English sentence saying what PASS proves).

## Where Sextant things live
- Manifests (proposed convention): `projects/tool-swarms/<run_name>.json`, tracked
  in the Sextant repo so runs are git-captured.
- Skeletons: `~/Projects/ringer/templates/` — browse its README first.
- Eval log / scoreboard: `~/.ringer/runs.jsonl` → `python3 ~/Projects/ringer/ringer.py models`.
- Artifacts (reviewed on the page, never cat'd): `~/.ringer/artifacts/`.

## Safety & egress
- Workers call EXTERNAL model APIs. The spec is the egress payload — keep spec
  content to what is acceptable to send off-machine. Never embed secrets, `.env`,
  credentials, or client-private brand data unless cleared.
- Workers never send outbound communications (draft-only; checks verify a draft
  exists, never that something was sent).
- Repo-editing swarms use worktrees mode: PASSING worktrees are DELETED — the
  check must export a patch (`git add -A && git diff --cached > <outside>.patch`
  inside the worktree); the orchestrator applies + commits after review.
- Never point workers at Sextant's memory stores (`context/`, `**/.command-centre/`).

## Boundary
Architecture DECISIONS stay out of the swarm. Ringer parallelizes research,
drafts, review, fixes, and consistency sweeps; choosing among alternatives is
operator-plus-orchestrator work.
```

> Adapt freely — trim the body, change the manifest-location convention, or match
> your description style. The one thing to preserve: a **trigger-rich
> description** (so routing fires) and the **pointer to the global playbook** (so
> you don't fork it).

### Step 3 — Register it (Agentic OS reconciliation)
Your `AGENTS.md` §"Skill & MCP Reconciliation" prescribes exactly this for a new
on-disk skill:
1. Add a row to `docs/skill-registry.md`.
2. Add a row to `docs/context-matrix.md` (brand_context files for this skill:
   **none** — it needs no brand context).
3. Add a `## tool-ringer` section to `context/learnings.md`.
4. Update `README.md`'s skills listing.
5. External-service scan: Ringer is a **local CLI**, not an external API service —
   so **no** Service Registry / `.env.example` entry is needed. (Its workers reach
   external APIs, but that's Ringer's own config at `~/.config/ringer/config.toml`,
   not a Sextant key.)

### Step 4 — Branch & commit
Per §"Branching Policy", `SKILL.md` is **Config** zone → prefer a feature branch:
`/new-feature ringer-skill` (or `/new-feature --quick` if you keep it to the skill
+ registration in one pass), merging back to `dev`. Content-only doc edits may go
straight to `dev`.

### Step 5 — Acceptance check (prove it worked)
- `.claude/skills/tool-ringer/SKILL.md` exists; frontmatter `name` == folder name.
- It appears in `bash scripts/list-skills.sh` and in `docs/skill-registry.md`.
- A fresh Sextant session, given a swarm-shaped batch (≥2 independent
  executably-checkable items), **routes to `tool-ringer`** rather than saying no
  skill matches.
- Engine smoke (Ringer side): `python3 ~/Projects/ringer/ringer.py demo --dry-run`
  runs clean. (The codex engine was repaired to 0.144.0 on 2026-07-09 after an
  XProtect cert-revocation removal; if a codex lane ever fails fast with `spawn
  ENOENT`, reinstall `@openai/codex@latest` — don't debug the model.)

---

## Phase 2 — cron wiring (optional, later)

Goal: let Sextant's scheduler run swarm-shaped batches unattended (the roadmap's
"swarm-shaped cron jobs call `ringer.py run`").

- Add a job under `cron/jobs` (or a `cron/templates` entry) that runs, e.g.:
  `python3 /Users/usjoh/Projects/ringer/ringer.py run <manifest.json>
  --no-dashboard --identity sextant-cron`.
- Store manifests in-repo (`projects/tool-swarms/<run_name>.json`).
- **Permissions:** Sextant's `.claude/settings.json` allows only `cat`, `ls`,
  `npm run *`, basic git, and edits to `/src/**`. Invoking `python3 ringer.py`
  relies on the machine-global allowlist (`Bash(python3 *)`) or needs an explicit
  allow entry — confirm before the first unattended run.
- **Safety:** the egress + worktrees + no-secrets rules from the skill apply
  doubly for unattended runs. Use `--identity sextant-cron` so concurrent swarms
  stay isolated, and keep `allow_full_access=false` in Ringer's config.

Treat Phase 2 as its own scoped task — do not bundle it with Phase 1.

---

## Boundaries / out of scope
- Do **not** port Meridian's `kos/*.yaml` files (Meridian-native; see above).
- Architecture decisions stay out of the swarm.
- Don't expose `.env`, credentials, or client-private data to worker specs.
- This spec lives in the Ringer repo; if it drifts from `ringer.py`, the CLI and
  its manifest dataclasses are authoritative (`TaskSpec` / `Manifest` in
  `ringer.py`), and the global skill is authoritative for the playbook.

## Reference — key paths & commands
| Thing | Value |
|---|---|
| Ringer engine | `/Users/usjoh/Projects/ringer/ringer.py` |
| Run a swarm | `python3 …/ringer.py run <manifest.json> [--max-parallel N] [--identity WHO]` |
| Ringside (screen first) | `python3 …/ringer.py hud` → http://127.0.0.1:8700 |
| Lint a manifest | `python3 …/ringer.py lint <manifest.json>` |
| Routing scoreboard | `python3 …/ringer.py models [--task-type <type>]` |
| Smoke test | `python3 …/ringer.py demo [--dry-run]` |
| Manifest skeletons | `/Users/usjoh/Projects/ringer/templates/` (README first) |
| Global playbook (authoritative) | `~/.claude/skills/ringer/SKILL.md` |
| Eval log / config | `~/.ringer/runs.jsonl` · `~/.config/ringer/config.toml` |
