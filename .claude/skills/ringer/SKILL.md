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

| Pattern | Shape | Use when |
|---|---|---|
| `review-swarm` | N read-only scouts, one surface each, each writes `report.md` | Whole-codebase or multi-surface review; one context can't hold it |
| `fix-swarm` | N workers in isolated git worktrees, executed build/test checks, patch export | Applying many independent fixes in parallel |
| `focus-group` | N persona workers each driving the real product via a harness script, in-character reaction + out-of-character graded eval | Product feedback, UX validation, prompt iteration |
| `bakeoff` | personas/tasks × candidate models matrix | Choosing a model or config with evidence, on the real surface |
| `research-with-proof` | research tasks + at least one task whose check EXECUTES the proof | Research where the deliverable must be true, not plausible |

Pattern-selection judgment:

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

**The engine choice belongs to the human.** Before the FIRST run of a job,
read what's actually wired up (`[engines.<name>]` blocks in
`~/.config/ringer/config.toml`) and ask the user which model should do the
typing — present the top 2–3 configured options with a one-line tradeoff
each and a recommendation, e.g.: *"Codex (strongest all-rounder, runs on
your existing plan — recommended for this job), GLM-5.2 via OpenCode
(20–30x cheaper, great for volume work), or a mix — heavy tasks on Codex,
mechanical ones on GLM?"* Honor their pick via the per-task `engine` field;
don't re-ask every round of the same job unless the mix isn't working.

Engines are config blocks (`[engines.<name>]` in config.toml), selectable
per task via the manifest `engine` field. Defaults are deliberate:

- **codex** (default): strongest general worker. Use per-task `engine_args`
  to set reasoning effort — spend it on hard tasks, not boilerplate.
- **opencode / GLM-class engines**: cheap intelligence for mechanical or
  high-volume work. Validate a new engine with a trivial one-task manifest
  before trusting it with a batch.
- Small/flash-class models are the first to choke on long conversational or
  multi-turn harness tasks — watch their retry counts before scaling them.
- Match `timeout_s` to the task: conversational harness tasks and
  build-and-test checks need far more than file edits.

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

1. Read the run JSON in `~/.ringer/runs/` — statuses, retries, durations.
2. For any retried or failed task, read the raw worker log in
   `<workdir>/logs/` before deciding anything. Retries that passed on
   attempt 2 often reveal a spec ambiguity worth fixing in your next
   manifest.
3. Spot-check at least one PASSING task's artifact per run. The check
   catches most laziness; you catch the rest.
4. Failures with useless error messages mean your CHECK needs work, not
   (only) the worker.

## Baked-in invariants (preserve in any change to ringer.py)

Stdin closed (`< /dev/null`); sandbox mode explicit; verification executes
the artifact; logs carry raw worker output only. These are load-bearing —
engine and invocation changes must keep all four.
