---
name: ringer
description: >-
  Orchestrate verified parallel work with Ringer from Codex. Load before
  running any model-backed probe, smoke test, simulation, grader, persona
  harness, or CLI agent; before an edit-test-edit loop or batch of similar
  edits; when reviewing failed worker output; and when writing or reviewing
  manifests, choosing swarm patterns or worker engines, or debugging a run.
  Codex coordinates while OpenCode plus OpenRouter is the implementation
  lane unless the user changes that policy. Skip for read-only file search,
  git operations, pure conversation or prose, and one one-file, few-line,
  one-shot edit; a second pass is a loop and triggers Ringer.
---

# Ringer orchestrator playbook (Codex host)

## Read this first — the four rules that actually get broken

1. **You review; workers type.** Your lane: specs, checks, pattern choice,
   reading results. If you are typing implementation, running probes, or
   babysitting a retry loop yourself, you have left your lane. The current
   Codex model stays in the coordinator seat; implementation is delegated.
2. **A single task is a one-task manifest.** Same verification, zero
   ceremony. "Too small for Ringer" is how drift starts — the smoke test,
   the probe script, the three-edit fix are all one-task manifests.
3. **Beware the tiny-edit death spiral.** The named anti-pattern: each step
   is individually small enough to justify inline, and two hours later the
   exception has become the workflow and nothing was verified or visible.
   The one-shot exception is ONE file, a few lines, ONCE. The second pass
   on the same problem is a loop, and loops are manifests.
4. **Runs are watched, not hidden — and the screen comes up FIRST.** The
   moment this skill loads for real work, before you write a single spec,
   put Ringside on the human's screen: `./ringer.py hud` (idempotent — if
   one is already up it says so and opens the page; runs also auto-start
   it). Ringside is the PAGE at http://127.0.0.1:8700 — NEVER launch the
   Ringside.app application (`open -a Ringside`); it is a parked prototype
   with a stale frontend. Never go dark: if your prep will take more than
   ~30 seconds, tell the human in one sentence what you're doing before
   you start. Never pass `--no-dashboard` except in automated tests or
   when the user explicitly asks.

Ringer runs manifest tasks in parallel across cheap CLI workers and
verifies every task by **executing a check command** — exit 0 is the only
PASS. Failed tasks are retried once with the check's actual failure output
injected into the retry prompt. You — the orchestrating model — pay tokens
only for specs, orchestration, and review.

```bash
./ringer.py lint manifest.json            # always lint before running
./ringer.py run manifest.json --identity <who-you-are>
./ringer.py demo                          # 3-worker smoke test
./ringer.py run manifest.json --dry-run   # print the plan, spawn nothing
```

Runs land in `~/.ringer/runs/`. Raw worker logs land in `<workdir>/logs/`.
Full reference: `README.md`. Ready-made manifest skeletons: `templates/`.

## The one exception: `ask`

Rule 2 holds for anything that changes a file, runs a build, or produces an
artifact worth checking. One lane does not fit it: the human asks a bounded,
read-only question over source you can already point at, and the answer is
prose. A manifest for that is ceremony, but answering it in your own context
means pulling whole files into a conversation that is already expensive.

```bash
./ringer.py ask "<the human's request>" --source /absolute/path/to/source
```

`ask` selects the passages that match the request, caps the packet, spawns one
clean worker on it, and allows a single attempt. Repeat `--source` for several
files or directories; `--state` takes a small file of settled decisions;
`--dry-run` shows the packet and spends nothing. If everything that matched is
too large for the packet it says so and stops before the model call rather than
letting a worker guess. A source small enough to fit whole is sent whole, so
choosing the sources is the work. Directory scans stay inside the named tree,
and symlinks leading out are skipped and reported. Runs appear on Ringside like
any other. `--redact` hides the request from Ringer's state and eval records,
but cannot scrub raw worker output, which is captured verbatim by design.

**Be honest about what it verifies.** The check is that `answer.md` exists and
is non-empty. That proves the worker answered, not that the answer is right.
You still read it.

**Everything else is a manifest.** Code changes, external actions, research
you intend to act on, and anything whose output a check could execute keep the
full path. If you could write a check that catches a wrong answer, write it and
make it a manifest.

## Preserve the lane policy

- Use the current Codex model for planning, manifest writing, check
  design, routing decisions, and post-run review.
- Set every implementation task to `"engine": "opencode"`.
- Use an OpenRouter model through the task's `"model"` field or the
  configured OpenCode default (`z-ai/glm-5.3-flash`). Do not create duplicate
  engine blocks or splice `-m` flags to change models — that is what the
  `model` field is for.
- Do not use Codex as a Ringer worker and do not use Grok unless the user
  explicitly changes the worker policy.
- Honor an explicit request to work inline. Otherwise, treat a single
  delegated task as a one-task manifest.

## One job, one artifact

A job the human asked for — however many rounds it takes — is ONE
artifact. Use the SAME `run_name` for every round (`sd-crate-launch`, not
`sd-crate-r1` / `sd-crate-r2`): the library accumulates each round as a
version under one entry, and the human watches one page evolve instead of
hunting across three "live" tabs. Name it after the JOB in the human's
words, not after your batch structure.

The artifact page is where results are REVIEWED. When a round finishes,
read the deliverables from the artifact store and direct the human to the
page — never `cat` result files into the terminal as the reveal. If a
result matters, it belongs in the artifact; if it isn't there, that's a
harvest gap to fix (declare it in `expect_files`), not a reason to bypass
the page.

## Spec-writing craft

Workers are stateless and cannot ask questions. Every spec must be
self-contained:

- **Open with the role and the boundary.** "You are a read-only scout…",
  "Your current working directory IS a git worktree of <repo> — edit
  files here directly." State what the worker must NEVER touch before what
  it should do.
- **Name every file the worker owns.** In multi-worker runs over one repo,
  file ownership must be disjoint — and disjoint across *all* concurrent
  lanes/branches, not just within one batch.
- **Embed the HOW TO RUN.** If the task drives a harness or script, put
  the exact command lines (with real absolute paths) in the spec. Workers
  should never have to discover an interface.
- **Define the output contract.** Say exactly which files to produce,
  where, and what each must contain. Graded/eval tasks should enumerate
  the grading criteria in the spec so the worker's output is checkable.
- **Hard rules travel in the spec, not in your head.** "Do NOT git
  commit", "never modify the repo, only write ./report.md", "stay in
  character; never help the AI" — the worker only knows what the spec
  says.
- **The spec is on camera.** Whoever is watching Ringside reads the spec
  as "what this agent was asked to do" — so write it as a self-contained,
  human-readable brief. Never write a pointer spec ("read /path/to/file
  and do what it says"): the watcher sees no brief, and the retry prompt
  loses the context it needs. Lint flags pointer specs.

## Check-writing rules

The check is the product. The retry prompt and the eval log both depend
on the check's failure output.

- **Checks must print WHY they fail.** `diff` beats `diff -q`; a
  validator script that prints which assertion broke beats `test -f`. A
  bare `test -f report.md` proves existence, not correctness.
- **Verify content, not existence.** Grep the artifact for required
  sections, run the code it produced, run the build, run the validator —
  execute something that would catch a lazy or hallucinated result.
- **`expect_files` is a floor, not the check.** List deliverables there
  for fast triage, but the check must still validate them.
- **Never `true`, `exit 0`, or `echo done`.** A check that cannot fail is
  a task that cannot be verified — that's just trusting the worker with
  extra steps.
- **Strict on substance, tolerant on format.** Verify what must be TRUE
  (the file proves X, the code runs, the quote exists in the source), use
  case-insensitive and flexible matching for structure, and reserve hard
  failure for substance: missing evidence, fabricated content, code that
  doesn't run.

## Pattern playbook

Reach for a named pattern before inventing one. Skeletons in
`templates/`. Inspect `templates/README.md` before drafting.

| Kit | Use when |
|---|---|
| review-swarm | Broad read-only review coverage before deciding what to fix. |
| fix-swarm | Confirmed independent fixes split across isolated worktrees. |
| focus-group | Isolated persona feedback on a product, pitch, prompt, or workflow. |
| bakeoff | Evidence for choosing a model, prompt, or configuration. |
| research-with-proof | Research backed by a proof task whose check executes the claim. |
| launch-kit | Go-to-market package across research, persona review, and assembly. |
| asset-swarm | Media assets produced in parallel with executable checks. |
| adversarial-review | Several models review the same artifact before synthesis. |
| repo-feature | Sandboxed workers edit a real repo with build and git checks. |
| migration-swarm | Partitioned mechanical codebase transforms across worktrees. |
| doc-swarm | Module docs with executed examples and checks against invented APIs. |
| test-hardening | Stronger tests by module while production source is off-limits. |
| competitive-teardown | Competitor research with citation allowlists and a synthesis phase. |
| data-pipeline | Fetch, transform, and validate stages with executed validators. |
| probe | A one-task manifest for a smoke, probe, or post-mortem. |

Pattern-selection judgment:

- **Review before fix.** Run a read-only review swarm, read the reports
  yourself, then compile the confirmed findings into a fix-swarm manifest.
  Don't let the same worker find and fix.
- **Personas must be separate workers.** Parallel personas in one context
  bleed into each other. One persona per task, one session dir per task.
- **Iterating on a prompt/product? Re-run the same panel.** A fixed
  persona panel across rounds tells you whether a change fixed what the
  panel actually complained about.
- **Probes, smokes, and diagnosis loops are manifests too.** A
  model-calling smoke test is a one-task manifest with the transcript as
  `expect_files` and a validator as the check. Diagnosing a failed
  worker's output is a read-only scout task.

## Engine selection

**The engine choice belongs to the human — but the recommendation comes
from THEIR evidence.** Before the FIRST run of a job: read what's wired up
(`[engines.<name>]` blocks in `~/.config/ringer/config.toml`), run
`./ringer.py models --task-type <this job's type>` for the local
scoreboard, and glance at `./ringer.py catalog --changes` for anything
newly free or newly cheap. Then ask the user which model should do the
typing — top 2–3 options with the NUMBERS in the pitch and a
recommendation. Honor their pick via the per-task `engine`/`model` fields;
don't re-ask every round of the same job unless the mix isn't working.
This is per-user by design: the scoreboard learns THIS user's workload —
never import another machine's conclusions.

**Explore or the scoreboard fossilizes.** In any run of 3+ tasks that has
a low-stakes lane (docs sweeps, mechanical edits, persona reviews — strong
executed check, retry to absorb failure), assign roughly ONE task to an
exploration candidate from `./ringer.py models --explore --task-type
<type>` (untested + cheap or free). Free promos from `catalog --changes`
jump the queue. Never explore on time-critical work. Promotion ladder
(computed by --explore): untested → probation → proven for a task_type
(3+ tasks, first-try ≥ 0.67). Record demotions in `docs/MODEL-NOTES.md`.

**OpenCode is the harness; the model is a manifest field.** Under this
host's lane policy every implementation task runs through the `opencode`
engine with the task's `"model"` field set to the OpenRouter slug — e.g.
`"engine": "opencode", "model":
"openrouter/z-ai/glm-5.3-flash"`. This holds even when someone —
including the user, in the heat of a run — says to "call kimi directly":
the harness is what provides the sandbox, raw logs, token counts, and
executed verification, so routing around it silently drops all four. Never
clone an engine block or splice `-m` through `engine_args` to change
models; that's what the `model` field is for.

- **Check the evidence before assigning models to tasks.** Run
  `./ringer.py models` — `first_try_pass_rate` is the routing signal;
  `pass_rate` includes retry rescues. Then read `docs/MODEL-NOTES.md` for
  the judgment the numbers can't carry.
- **"Show me the scoreboard" is one command.** When the human asks to see
  the model scoreboard, run `./ringer.py models --open` — it renders the
  full scoreboard as a zero-LLM HTML page and opens it. Costs no tokens;
  never hand-summarize the numbers when the page can show them.
- **Give every task a `task_type`** (canonical vocabulary in the README).
  Untyped tasks bucket as (untyped) and teach the scoreboard nothing; lint
  nudges you when it's missing.

## Worktrees-mode footguns (learned the hard way)

Run-level `"worktrees": true` gives each task an isolated git worktree of
`repo`, detached at HEAD. Four consequences:

1. **Passing tasks get their worktree DELETED.** Deliverables must land
   outside the task worktree, or the check must export them first.
2. **Worker commits die with the worktree.** Pattern that works: the
   worker leaves changes uncommitted; the check runs
   `git add -A && git diff --cached > <path-outside-worktree>.patch` and
   validates the patch. You apply and commit on your branch after review.
3. **Logs survive** (they go to `<workdir>/logs/`), so post-mortems work
   even on deleted worktrees.
4. **Gitignored outputs silently vanish from patch exports.** `git add -A`
   cannot stage ignored files (build dirs like `dist/`). If a task touches
   any gitignored path, the check must `cp` those files to a path outside
   the worktree explicitly — verify the patch AND the copies.

When integrating patches into the real repo, stage specific paths — never
`git add -A` in a checkout that may hold someone's untracked scratch
files.

## Lint, preview, and run

```bash
./ringer.py lint manifest.json
./ringer.py run manifest.json --dry-run
./ringer.py run manifest.json --identity codex-orchestrator
```

Fix every lint error and resolve meaningful warnings before running. For
deployment, destructive changes, credential use, or broad external writes,
show the execution plan and obtain any missing authority before the
mutating task begins. Do not hide a manual worker loop behind shell
commands — Ringer owns spawning, stdin closure, retries, logging, and
checks.

## Review the executed evidence

After each run:

1. Read the run state in `~/.ringer/runs/` — statuses, retries, durations.
2. Read raw logs in `<workdir>/logs/` for every failed or retried task.
   Retries that passed on attempt 2 often reveal a spec ambiguity.
3. Spot-check at least one PASSING task's artifact against source or
   behavior.
4. Review deliverables from the artifact page and direct the user to
   Ringside — never `cat` results into the terminal as the reveal.
5. Distinguish worker failure from a spec/check collision.
6. Failures with useless error messages mean your CHECK needs work.
7. **Update `docs/MODEL-NOTES.md`** when a run taught a reusable routing
   lesson: one dated line under the model — task type, what happened, what
   you'd do differently. Only what the executed checks and raw logs
   support.

Do not accept worker completion claims as proof. PASS means Ringer
executed the task check successfully. If the check is weak, strengthen it
and run another version under the same `run_name`.

## Spend your own context deliberately

The scoreboard exists so that worker tokens buy evidence. Your own tokens are
not free either, and nothing in the tool constrains them:

- **Reach for code before a model.** Counting, sorting, exact-text search,
  field extraction, format conversion, file comparison and validation belong
  with `rg`, `jq`, a parser or a small script.
- **Select passages; do not load files.** Search first, then read what matched.
  `ask` does this for you. When you are not using `ask`, do it by hand.
- **Load a tool when the job needs it**, not every connector and schema at the
  top of a session on the chance that one gets used.
- **Answer the question that was asked.** Use a sentence when a sentence was
  requested. Avoid process diaries, restating the request and unrequested
  options.
- **Never retry into a limit.** A token or usage-limit failure is not
  transient. Reduce the input or take a cheaper path.

When claiming a saving, count the whole job, including planning and review.
Moving tokens from your context into a worker only saves tokens if the total
comes down.

## Baked-in invariants (preserve in any change to ringer.py)

These are load-bearing — engine and invocation changes must keep all of
them:

- **Worker stdin is closed** (`< /dev/null`) — headless CLI agents hang
  forever waiting on a TTY that isn't there.
- **Sandbox mode is always explicit** — default sandboxes silently resolve
  to read-only in temp directories and block every artifact write.
- **Verification executes the artifact** — an agent's own "done" is not
  evidence. Exit codes are.
- **Raw output only** — logs and eval rows carry verbatim worker output,
  never a summary.
- **Retry context includes the actual check failure** — the retry prompt
  carries the check's failure output so the worker can fix against it.
- **Hooks only nudge and never block the host session** — the Ringer hooks
  emit non-blocking `additionalContext` and never refuse a tool call.

Read `README.md` for the full manifest reference and `docs/MODEL-NOTES.md`
for local routing history when needed.
