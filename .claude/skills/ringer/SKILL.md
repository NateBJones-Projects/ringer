---
name: ringer
description: >-
  Orchestrator playbook and routing rules for Ringer, the verified-swarm
  delegation tool (ringer.py). TRIGGER — load BEFORE acting, not after —
  whenever: you are about to run ANY script or command that calls a model or
  drives a conversational/eval harness (probe, smoke test, simulation,
  grader, persona conversation) outside a live Ringer run; you are about to
  start an edit→test→edit loop or a batch of similar edits across files; you
  are about to do a "quick check" that spawns a model or a CLI agent; you are
  reviewing or diagnosing failed worker or model output; you catch yourself
  thinking a task is "small enough to just do myself" — that thought IS the
  trigger (a single task is a one-task manifest); or you are writing or
  reviewing a manifest, choosing a swarm pattern (review swarm, fix swarm,
  focus group, bakeoff, research-with-proof), picking a worker engine, or
  debugging a failed run. SKIP only for: reading or searching files, git
  operations, a one-file few-line ONE-SHOT edit (once — if you are back for a
  second pass, that is a loop: TRIGGER), authoring prose/specs/docs straight
  from your own context, or pure conversation.
---

# Ringer orchestrator playbook

## Read this first — the four rules that actually get broken

1. **You review; workers type.** Your lane: specs, checks, pattern choice,
   reading results. If you are typing implementation, running probes, or
   babysitting a retry loop yourself, you have left your lane.
2. **A single task is a one-task manifest.** Same verification, zero
   ceremony. "Too small for Ringer" is how drift starts — the smoke test,
   the probe script, the three-edit fix are all one-task manifests.
3. **Beware the tiny-edit death spiral.** The named anti-pattern: each step
   is individually small enough to justify inline, and two hours later the
   exception has become the workflow and nothing was verified or visible.
   The one-shot exception is ONE file, a few lines, ONCE. The second pass on
   the same problem is a loop, and loops are manifests.
4. **Runs are watched, not hidden — and the screen comes up FIRST.** The
   moment this skill loads for real work, before you write a single spec,
   put Ringside on the human's screen: `./ringer.py hud` (idempotent — if
   one is already up it says so and opens the page; runs also auto-start
   it). Ringside is the PAGE at http://127.0.0.1:8700 — NEVER launch the
   Ringside.app application (`open -a Ringside`); it is a parked prototype
   with a stale frontend. And never go dark: if your prep (research,
   check-writing, manifest drafting) will take more than ~30 seconds,
   tell the human in one sentence what you're doing and roughly how long
   before you start — they should be watching the empty arena and reading
   your one-liner, not wondering if anything is happening. Never pass
   `--no-dashboard` except in automated tests or when the user explicitly
   asks.

Ringer runs manifest tasks in parallel across cheap CLI workers (Codex,
OpenCode/GLM, others via config) and verifies every task by **executing a
check command** — exit 0 is the only PASS. Failed tasks are retried once
with the check's actual failure output injected into the retry prompt. You —
the orchestrating model — pay tokens only for specs, orchestration, and
review.

```bash
./ringer.py lint manifest.json            # always lint before running
./ringer.py run manifest.json --identity <who-you-are>
./ringer.py demo                          # 3-worker smoke test
./ringer.py run manifest.json --dry-run   # print the plan, spawn nothing
```

Runs land in `~/.ringer/runs/`. Raw worker logs land in `<workdir>/logs/`.
Full reference: `README.md`. Ready-made manifest skeletons: `templates/`.
Lint catches unverifiable checks, silent checks, worktree deliverable/commit
loss, serial fan-out, write collisions, and underspecified specs; `run`
prints the same findings as non-blocking warnings.

## One job, one artifact

A job the human asked for — however many rounds it takes — is ONE artifact.
Use the SAME `run_name` for every round (`sd-crate-launch`, not
`sd-crate-r1` / `sd-crate-r2`): the library accumulates each round as a
version under one entry, and the human watches one page evolve instead of
hunting across three "live" tabs. Name it after the JOB in the human's
words, not after your batch structure.

And the artifact page is where results are REVIEWED. When a round finishes,
read the deliverables from the artifact store and direct the human to the
page — never `cat` result files into the terminal as the reveal. If a result
matters, it belongs in the artifact; if it isn't there, that's a harvest gap
to fix (declare it in `expect_files`), not a reason to bypass the page.

## Spec-writing craft

Workers are stateless and cannot ask questions. Every spec must be
self-contained:

- **Open with the role and the boundary.** "You are a read-only scout…",
  "Your current working directory IS a git worktree of <repo> — edit files
  here directly." State what the worker must NEVER touch before what it
  should do.
- **Name every file the worker owns.** In multi-worker runs over one repo,
  file ownership must be disjoint — and disjoint across *all* concurrent
  lanes/branches, not just within one batch. Every file a spec mentions must
  be in that worker's ownership list.
- **Embed the HOW TO RUN.** If the task drives a harness or script, put the
  exact command lines (with real absolute paths) in the spec. Workers should
  never have to discover an interface.
- **Define the output contract.** Say exactly which files to produce, where,
  and what each must contain. Graded/eval tasks should enumerate the grading
  criteria in the spec so the worker's output is checkable.
- **Hard rules travel in the spec, not in your head.** "Do NOT git commit",
  "never modify the repo, only write ./report.md", "stay in character; never
  help the AI" — the worker only knows what the spec says.
- **The spec is on camera.** Whoever is watching Ringside reads the spec as
  "what this agent was asked to do" — so write it as a self-contained,
  human-readable brief. Never write a pointer spec ("read /path/to/file and
  do what it says"): the watcher sees no brief, and the retry prompt loses
  the context it needs. Point at files for source MATERIAL; the instructions
  themselves live in the spec. Lint flags pointer specs.

## Check-writing rules

The check is the product. The retry prompt and the eval log both depend on
the check's failure output.

- **Checks must print WHY they fail.** `diff` beats `diff -q`; a validator
  script that prints which assertion broke beats `test -f`. A bare
  `test -f report.md` proves existence, not correctness.
- **Verify content, not existence.** Grep the artifact for required sections,
  run the code it produced, run the build, run the validator — execute
  something that would catch a lazy or hallucinated result.
- **`expect_files` is a floor, not the check.** List deliverables there for
  fast triage, but the check must still validate them.
- **Never `true`, `exit 0`, or `echo done`.** A check that cannot fail is a
  task that cannot be verified — that's just trusting the worker with extra
  steps.
- **Strict on substance, tolerant on format.** Checks that count exact
  headings, demand exact casing, or grep rigid phrasings fail honest work
  over formatting — and a wall of red format-failures reads as a broken
  system, not a careful one (demo-night lesson). Verify what must be TRUE
  (the file proves X, the code runs, the quote exists in the source), use
  case-insensitive and flexible matching for structure, and reserve hard
  failure for substance: missing evidence, fabricated content, code that
  doesn't run.

## Pattern playbook

Reach for a named pattern before inventing one. Skeletons in `templates/`:

| Kit | Use when |
|---|---|
| [review-swarm](../../../templates/review-swarm/) | You need broad read-only review coverage before deciding what to fix. |
| [fix-swarm](../../../templates/fix-swarm/) | You have confirmed independent fixes that can be split across isolated worktrees. |
| [focus-group](../../../templates/focus-group/) | You need isolated persona feedback on a product, pitch, prompt, or workflow. |
| [bakeoff](../../../templates/bakeoff/) | You need evidence for choosing a model, prompt, or configuration across shared scenarios. |
| [research-with-proof](../../../templates/research-with-proof/) | You need research backed by a proof task whose check executes the claim. |
| [launch-kit](../../../templates/launch-kit/) | You need a go-to-market package built across research, persona review, and final assembly rounds. |
| [asset-swarm](../../../templates/asset-swarm/) | You need media assets produced in parallel with executable checks for renders, batches, diagrams, or captures. |
| [adversarial-review](../../../templates/adversarial-review/) | You want several models to review the same artifact before the orchestrator synthesizes findings. |
| [repo-feature](../../../templates/repo-feature/) | You know what to build and need sandboxed workers to edit a real repo with build and git checks. |
| [migration-swarm](../../../templates/migration-swarm/) | You have mechanical codebase transforms that can be partitioned across worktrees. |
| [doc-swarm](../../../templates/doc-swarm/) | You need module docs with executed examples and checks against invented APIs. |
| [test-hardening](../../../templates/test-hardening/) | You need stronger tests by module while keeping production source edits off-limits. |
| [competitive-teardown](../../../templates/competitive-teardown/) | You need competitor research with citation allowlists and a synthesis phase. |
| [data-pipeline](../../../templates/data-pipeline/) | You need fetch, transform, and validate stages with executed validators and honesty rules. |
| [probe](../../../templates/probe/) | You need a one-task manifest for a smoke, probe, or post-mortem. |

Pattern-selection judgment:

- **Browse the catalog first.** Before writing any manifest, browse
  `templates/README.md`: choose a kit, mix pieces from several, or write
  your own having seen the prior art.
- **Review before fix.** Run a read-only review swarm, read the reports
  yourself, then compile the confirmed findings into a fix-swarm manifest.
  Don't let the same worker find and fix.
- **Personas must be separate workers.** Parallel personas in one context
  bleed into each other. One persona per task, one session dir per task.
- **Iterating on a prompt/product? Re-run the same panel.** A fixed persona
  panel across rounds tells you whether a change fixed what the panel
  actually complained about.
- **Probes, smokes, and diagnosis loops are manifests too.** A model-calling
  smoke test is a one-task manifest with the transcript as `expect_files`
  and a validator as the check. Diagnosing a failed worker's output is a
  read-only scout task. If it calls a model, it runs under Ringer — that is
  what makes it visible, verified, and logged.

## Engine selection

**The engine choice belongs to the human — but the recommendation comes
from THEIR evidence.** Before the FIRST run of a job: read what's wired up
(`[engines.<name>]` blocks in `~/.config/ringer/config.toml`), run
`./ringer.py models --task-type <this job's type>` for the local scoreboard,
and glance at `./ringer.py catalog --changes` for anything newly free or
newly cheap. Then ask the user which model should do the typing — top 2–3
options with the NUMBERS in the pitch and a recommendation, e.g.: *"GLM is
6/6 first-try on persona work here at ~2¢/task — recommended. Codex is also
100% but ~8x the tokens. And kimi went free on OpenRouter yesterday — want
it auditioning one of the small tasks?"* Honor their pick via the per-task
`engine`/`model` fields; don't re-ask every round of the same job unless
the mix isn't working. This is per-user by design: the scoreboard learns
THIS user's workload — never import another machine's conclusions or
recommend from a different user's numbers.

**Explore or the scoreboard fossilizes.** Always recommending the proven
pick means never learning a new one. In any run of 3+ tasks that has a
low-stakes lane (docs sweeps, mechanical edits, persona reviews — strong
executed check, retry to absorb failure), assign roughly ONE task to an
exploration candidate from `./ringer.py models --explore --task-type <type>`
(untested + cheap or free, text-capable, decent context). Free promos from
`catalog --changes` jump the queue — a temporarily-free model is a zero-cost
experiment. Never explore on time-critical work, never with more than a
small slice of a batch, and name the experiment when presenting the engine
ask so the human can veto it. Promotion ladder (computed by --explore):
untested → probation (some evidence) → proven for a task_type (3+ tasks,
first-try ≥ 0.67). Proven models earn bigger lanes in that type and an
audition one rung up in adjacent types; repeated first-attempt failures end
the audition — record the demotion in MODEL-NOTES so the next orchestrator
doesn't re-run the experiment.

**OpenCode is the harness; the model is a manifest field.** Unless a model
ships its own first-class harness (Codex does), it runs through the
`opencode` engine with the task's `"model"` field set to the OpenRouter
slug — e.g. `"engine": "opencode", "model": "openrouter/moonshotai/kimi-k2.7-code"`.
This holds even when someone — including the user, in the heat of a run —
says to "call kimi directly" or reach for the model's own CLI: the harness
is what provides the sandbox, raw logs, token counts, and executed
verification, so routing around it silently drops all four. Never clone an
engine block or splice `-m` through `engine_args` to change models; that's
what the `model` field is for, and a bakeoff is only real when the MANIFEST
names each competitor (2026-07-06 lesson: an engine block with a hard-coded
model ran one model under three competitors' names).

Engines are config blocks (`[engines.<name>]` in config.toml), selectable
per task via the manifest `engine` field. Defaults are deliberate:

- **codex** (default): strongest general worker. Use per-task `engine_args`
  to set reasoning effort — spend it on hard tasks, not boilerplate.
- **opencode**: the universal lane — any OpenRouter model via the `model`
  field (engine `model_default` is GLM-5.2, the cheap-intelligence pick).
  Validate a model new to you with a trivial one-task manifest before
  trusting it with a batch.
- Small/flash-class models are the first to choke on long conversational or
  multi-turn harness tasks — watch their retry counts before scaling them.
- Match `timeout_s` to the task: conversational harness tasks and
  build-and-test checks need far more than file edits.
- **Check the evidence before assigning models to tasks.** Run
  `./ringer.py models` (optionally `--task-type <type>`) — the local
  scoreboard aggregating every executed-check outcome per (model,
  task_type): first_try_pass_rate is the routing signal; pass_rate includes
  retry rescues. Then read `docs/MODEL-NOTES.md` (in the ringer repo) for
  the judgment the numbers can't carry. Routing is grounded in performance,
  not vibes (Jon directive 2026-07-06).
- **"Show me the scoreboard" is one command.** When the human asks to see
  the model scoreboard, rankings, model costs, or "which models work best,"
  run `./ringer.py models --open` — it renders the full scoreboard (tiers,
  first-try rates, est. $/task, usage, MODEL-NOTES excerpts, free-promo
  watchlist) as a zero-LLM HTML page in the artifact library and opens it
  in their browser. Costs no tokens; never hand-summarize the numbers when
  the page can show them.
- **Give every task a `task_type`** (canonical vocabulary in the README —
  code-feature, code-fix, code-review, research, persona-review, site-build,
  image-gen, docs, probe, bakeoff, ...). Untyped tasks bucket as (untyped)
  and teach the scoreboard nothing; lint nudges you when it's missing.

## Feeder wire-class contract (THIS MACHINE — fleet directive, Adam-approved 2026-07-14)

On this box every real worker's LLM is served by Feeder (`http://localhost:3001/v1`
via the `opencode` engine). Feeder does NOT validate the wire class — an
unrecognised `auto/<class>` SILENTLY degrades to the generic `overall` prior in
both routing and the quality feed. Enforcement is the orchestrator's job:

1. **The wire vocabulary is exactly 7 tokens**: `coding`, `math`, `reasoning`,
   `creative_writing`, `instruction_following`, `long_query`, `multi_turn`.
   Never use Feeder's aliases (code/writing/puzzle/long/...); never pin
   `overall`. Bare `auto` (= overall) only for genuinely unclassifiable tasks.
2. **Every feeder-routed task carries BOTH fields**: local `task_type` (Ringer's
   native vocab, local scoreboard) AND an explicit `"wire_class"` — with
   `"model": "feeder/auto/<wire_class>"` agreeing. The orchestrator decides the
   wire_class per task (default map in `scripts/wire_class.py --map`; ambiguous
   types — docs, bakeoff — are decided per task, recorded in the manifest,
   never re-guessed at runtime).
3. **Validate before every run**: `python3 scripts/wire_class.py <manifest>`
   alongside `./ringer.py lint`. Periodically (and before any non-coding run):
   `scripts/wire_class.py --check-enum` — the no-drift check against Feeder's
   live canon.
4. **Pre-launch capacity ritual (hard-refuse rule)**: every concurrent worker
   gets a DISTINCT provider platform, so before launching a swarm run
   `python3 scripts/swarm_capacity.py <wire_class> <manifest_max_parallel>` and
   pass the printed number as `--max-parallel`; re-query per round. If the
   endpoint isn't up, fall back to the manifest value and rely on Feeder
   backpressure.
5. **Quality feedback keys on wire_class** (Feeder's task_scores granularity),
   never on local task_type — two scoreboards at two granularities, deliberately.

## Worktrees-mode footguns (learned the hard way)

Run-level `"worktrees": true` gives each task an isolated git worktree of
`repo`, detached at HEAD. Three consequences:

1. **Passing tasks get their worktree DELETED.** Deliverables must land
   outside the task worktree, or the check must export them first.
2. **Worker commits die with the worktree.** Pattern that works: the worker
   leaves changes uncommitted; the check runs
   `git add -A && git diff --cached > <path-outside-worktree>.patch` and
   validates the patch. You apply and commit on your branch after review.
3. **Logs survive** (they go to `<workdir>/logs/`), so post-mortems work
   even on deleted worktrees.
4. **Gitignored outputs silently vanish from patch exports.** `git add -A`
   cannot stage ignored files (build dirs like `dist/`), so a worker's edits
   there pass its checks, export an incomplete patch, and die with the
   worktree. If a task touches any gitignored path, the check must `cp`
   those files to a path outside the worktree explicitly — verify the patch
   AND the copies before trusting the run.

And on your own side of the fence: when integrating patches into the real
repo, stage specific paths — never `git add -A` in a checkout that may hold
someone's untracked scratch files.

## Post-run review ritual

0. **Enrich Feeder-routed runs first** (this machine): `python3 scripts/feeder_enrich.py
   --state ~/.ringer/runs/<run_id>.json --workdir <workdir>` pulls each worker's true
   routing (served models, failovers, latency) from Feeder into the state, and the
   report pages then show a "Served by" strip per task. Re-render a finished run's
   pages with ArtifactRenderer if you enriched after it closed.
1. Read the run JSON in `~/.ringer/runs/` — statuses, retries, durations.
2. For any retried or failed task, read the raw worker log in
   `<workdir>/logs/` before deciding anything. Retries that passed on
   attempt 2 often reveal a spec ambiguity worth fixing in your next
   manifest.
3. Spot-check at least one PASSING task's artifact per run. The check
   catches most laziness; you catch the rest.
4. Failures with useless error messages mean your CHECK needs work, not
   (only) the worker.
5. **Update `docs/MODEL-NOTES.md`** (in the ringer repo) when a run taught
   you something about a model: one dated line under the model — task type,
   what happened (attempts, tokens, failure mode), what you'd do
   differently. Only what the executed checks and raw logs support. The raw
   numbers took care of themselves — every attempt already landed in the
   local model log (`./ringer.py models` to see the updated scoreboard).

## Grading rubric — the quality feed (THIS MACHINE, Adam's directive 2026-07-14)

After the post-run review, grade each worker's output 0..1 and feed it to the fleet's
routing brain: `python3 scripts/quality_feed.py --state ~/.ringer/runs/<run>.json
--grade <key>=<score> ... --post` (enrich the state first — ritual step 0). The score
is your considered judgment of BOTH the executed-check result and output quality,
keyed to the concrete served model. Anchors — stay on them, the EWMA amplifies
grader drift:

- **1.0** — pass first-try, clean and idiomatic; nothing you'd fix.
- **0.85** — pass first-try, unremarkable (the ungraded-pass default is 0.8).
- **0.5** — pass-but-poor: retry-rescued, ugly, overcomplicated, barely scraped
  through the check.
- **0.2** — fail, but with salvageable partial work you actually reused.
- **0.0** — fail, fabrication, or no honest attempt.

Rules: grade the outcome, not the effort; mixed-model attempts are auto-skipped
(never re-attribute them — the skip count doubles as an anti-affinity health
metric); backpressured attempts score nobody; grade only what the checks and raw
logs support.

## Baked-in invariants (preserve in any change to ringer.py)

Stdin closed (`< /dev/null`); sandbox mode explicit; verification executes
the artifact; logs carry raw worker output only. These are load-bearing —
engine and invocation changes must keep all four.
