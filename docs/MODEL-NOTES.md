# Model notes — how workers actually perform

A running log of how models perform on real Ringer tasks, so engine and
model choices are made on evidence instead of vibes. The raw numbers now
live in the local eval log (`~/.ringer/runs.jsonl`); run `./ringer.py models`
to print the per-model, per-task_type scoreboard (tasks, attempts,
pass_rate, first_try_pass_rate, median duration/tokens, last_seen). This
file remains the judgment layer on top of those numbers.

**How to add a row:** after reviewing a run (post-run ritual step 5 in the
ringer skill), append one dated line under the model. Say the task type,
what happened, and what you'd do differently. Only write what the executed
checks and raw logs support — no vibes, no worker self-reports.

## codex (GPT-5-class, own harness)

- Strongest general worker; the default engine. Spend reasoning effort per
  task via `engine_args` (`["-c", "model_reasoning_effort=low|medium|high"]`)
  — high on gnarly tasks, low on boilerplate.
- 2026-07-05 — carried the heavy lanes of the milk-crate demo rehearsals
  (market read with source allowlist, site build) with clean first-attempt
  passes.
- 2026-07-10 — gpt-5.6-sol, code-feature (steering-profiles feature in
  ringer.py itself, ~470-line change + 18 tests + docs, run
  ringer-steering-profiles): shipped as PR #25. 2 attempts, 379k tokens,
  but the attempt-1 FAIL was the CHECK's fault, not the model's — the check
  gated on the ENTIRE pre-existing suite being green inside the worker
  sandbox (localhost binds blocked, fixture missing). The feature work
  itself was verified green both attempts; attempt 2 "hardened" an already
  -sound implementation. Scoreboard's FAIL row for this run understates the
  model. Lesson for check authors: regression gates must compare against
  the BASELINE failure set, never assert absolute suite green.
- 2026-07-06 — adversarial pre-merge review (aicred spark): passed on
  attempt 1, ~85k tokens.
- 2026-07-06 — motion design (5 HTML animations for video b-roll) + 2
  editorial diagram pages, each verified by rendering through headless
  Chromium to MP4/PNG: 7/7 passed on attempt 1. Broadcast-quality visual
  output from rich storyboard specs; the render-as-check pattern works.
- 2026-07-06 — milk-crate demo: two single-file website builds (v1 scaffold
  316s/~175k tok; final brand+market-test reskin 622s/~184k tok), both passed
  14-assertion content checks on attempt 1, including base64-embedding photos
  and honoring honesty-marker requirements. Codex remains the site-build lane.
- 2026-07-06 — ringer.py feature batch (task_type field + enriched eval rows
  + `models` scoreboard + hud single-tab fix; ~640-line diff incl. two new
  test suites): substance passed on attempt 1 — its check printed PASS
  (compile, all 16 suites, exact CLI aggregation contract) — but the run
  recorded attempt 2 because of the expect_files-before-check harness bug
  (see process lessons). Heavy single-file feature work against an exact
  behavioral contract is squarely codex's lane.

- 2026-07-06 — elsas-website demo: Next.js scaffold PASSED attempt 2 (682s,
  ~354k tok) — attempt 1 built a complete homepage and silently skipped the
  other 10 routes; the route-enumeration check caught it. Narration lane
  (15 ElevenLabs calls, chunked, nohup pattern) passed attempt 1. CAUTION: a
  codex fix worker GAMED a verbatim-content needle by hiding the required text
  in a visually-hidden paragraph — passed the check, caught only by
  orchestrator integration review. Needle checks need an anti-hidden-text
  assertion or documented exceptions.

- 2026-07-06 — OpenRouter catalog + explore suggester (catalog subcommand
  with snapshot/changelog/free-detection, daemon auto-refresh, tiered
  --explore; offline fixture-driven contract check): PASS attempt 1, 362s.
  Follow-up sentinel-pricing fix (variable-pricing models): PASS attempt 1,
  114s. With the verify-order fix landed, zero phantom retries across the
  whole batch.
- 2026-07-06 — adversarial review of the model-router stack (2,650-line
  diff, structured report contract): PASS attempt 1, 176s — found a real
  HIGH (--since window inflating first-try rates) plus 3 MEDIUMs, all
  confirmed against the code. Then fixed all five review findings in one
  batch (task-level --since, pricing transitions, event durability + flock,
  unknown pricing, stderr notice) with test coverage: PASS attempt 1, 202s.
  Review->fix roundtrip in codex's lane works end to end.
- 2026-07-06 — scoreboard HTML page (zero-LLM renderer, ~700-line diff,
  design + evidence-floor ranking + cost math + notes parser): substance
  PASS attempt 1 (the run's recorded retry was an orchestrator check bug —
  the free-promo watchlist legitimately mentions a free model before the
  ranked cards, and the check compared raw first-occurrence). Six review
  findings fixed in one batch, PASS attempt 1, 141s.
- 2026-07-06 — model-db stack (SQLite read model 516s, page redesign 536s,
  Ringside tab 527s, plus three fix batches all attempt-1): five substantial
  ringer.py features in one day, every one against an executed contract
  check. Review lane found the HIGH that mattered (sync cursor skipping a
  half-written trailing line). Codex is the proven lane for both sides of
  the review->fix loop on this codebase.

## glm-5.2 via opencode (`openrouter/z-ai/glm-5.2`)

- The cheap-intelligence default (~$0.74/M in, $2.33/M out, 2026-07 —
  20-30x cheaper output than frontier coding models). Reliable on
  mechanical, tightly-specced work: file edits, format conversions,
  template-driven builds.
- 2026-07-05 — milk-crate demo rehearsals: handled brand-board/SVG/copy
  tasks at around a penny per passing task.
- 2026-07-06 — adversarial pre-merge review (aicred spark): passed, but
  needed the retry (attempt 2) where codex passed on attempt 1. Long
  structured reviews sit at the edge of its comfort zone; keep the section
  contract explicit in the spec.
- 2026-07-06 — three mechanical image-generation batches (18 images via
  openrouter-image commands, idempotent batch-runner spec): 3/3 passed on
  attempt 1, ~14.5k tokens each. The "execute these exact commands, do not
  improve them" spec pattern is fully reliable for glm-5.2.

- 2026-07-06 — backfill/seed script for the model log (252-line stdlib CLI
  with a run-state join, 3-level mapping precedence, never-overwrite and
  idempotency rules): the artifact was CORRECT; the recorded FAIL was an
  orchestrator check-fixture bug (a missing newline glued the fixture's last
  row to a garbage line) plus the harness ordering bug below. Verified PASS
  once the check was fixed. Tight behavior contracts in the spec work great
  for glm — and read the raw logs before blaming the model.
- 2026-07-06 — README/MODEL-NOTES docs + task_type sweep across 17 template
  manifests: passed attempt 2; attempt 1 was lost to the harness ordering
  bug, not model quality — the retry worker's log correctly diagnosed that
  harness bug unprompted, impressive debugging from the cheap lane.
- 2026-07-06 — catalog/explore README section (flags, promotion ladder,
  per-user framing): PASS attempt 1, ~21.5k tokens. Doc sections against a
  grep-able content contract remain a safe glm lane.
- 2026-07-06 — milk-crate demo, full run: 4 independent buyer-persona
  reviews (focus group) all passed attempt 1 (~15k tokens, ~2¢ each) with an
  explicit VERDICT-block contract — persona work is squarely in glm's zone.
  Market read with live curl fetching passed once the spec demanded verbatim
  copy-paste of source URLs (first fail was the worker trimming URL slugs —
  spec/check craft, not model weakness). Brand-kit doc incl. a clean inline
  SVG wordmark: good, one bounce off an over-strict check regex.

- 2026-07-06 — elsas-website demo: verbatim content capture (16 pages + 19
  news posts, 213 blockquotes) passed attempt 2 — attempt 1 SELF-REPORTED
  "all 213 match exactly, 0 errors" while the executed check found 13 stitched/
  paraphrased quotes. Self-reports are worthless; the retry with injected
  failures fixed all 13 (~148k tok total, ~3¢). Page builds (about+faq;
  news index + 19 generated post routes via its own extraction script) and
  2 focus-group personas: all attempt 1. Fix batch attempt 1.
- 2026-07-06 — invariants/file-I/O review lens on the same stack: PASS
  attempt 1, 68k tokens — caught the non-atomic backfill rewrite (real data
  loss risk) and the daemon stdout race; both confirmed. Then fixed the
  backfill atomicity (tmp+os.replace, pid-stamped backups) attempt 1 with
  the original behavioral grader unchanged. Structured review with an
  explicit lens is now proven glm territory, not just probation.
- 2026-07-06 — solo adversarial review of the scoreboard renderer (~700
  line diff, injection-focused lens): PASS attempt 1 — 1 MEDIUM (unanchored
  MODEL-NOTES heading match cross-contaminating gpt-4/gpt-4o-style
  families) + 5 real LOWs, plus an empirically-verified injection all-clear
  (it actually rendered hostile model ids to prove escaping). Second
  proven-tier structured review in one day; glm is now the default review
  lane for mid-size diffs.
- 2026-07-06 — invariants/injection/frontend review of the 4,061-line
  model-db branch: PASS attempt 1, 96k tokens, 14 coverage items — two real
  contention findings (full catalog re-ingest per sync; schema writes on
  read paths) plus an empirical XSS all-clear on the new DOM surfaces.
  Third proven-tier structured review today.

## kimi-k2.7 via opencode (`openrouter/moonshotai/kimi-k2.7-code`)

- 2026-07-06 — adversarial pre-merge review (aicred spark): passed on
  attempt 1, ~83k tokens. First real outing; promising for review work.
  (Ran through an ad-hoc copy of the opencode engine block — the per-task
  `model` field now makes that unnecessary.)

## kimi-k2.6 (`moonshotai/kimi-k2.6`, subject-model evidence via OpenRouter)

- 2026-07-07 — Benchmark Suite 2.0 operator eval, killed by Jon at ~4.5h.
  Serving throughput, not model quality, was the failure: on the Brick
  1000-piece case (reasoning xhigh, pinned provider order
  inceptron→decart→baidu→modelrun, no fallbacks) K2.6 averaged ~21 tok/s
  with two ~19-min stalls at 4.5 tok/s — 136+ min unfinished vs Sonnet 5's
  25 min (94 tok/s) and GPT-5.5's 24 min (55 tok/s) on the identical case.
  Model behavior itself was fine: 28 turns (fewer than Sonnet's 82), 170k
  output tokens (in family norms), 12% reasoning, zero API errors. Verdict:
  do NOT schedule K2.6 for long agentic work through that provider set;
  if K2.6 data is ever wanted, probe a single case against other providers
  first. Distinct model from k2.7-code above — don't transfer this verdict
  to k2.7.


## grok-build (Grok CLI engine, flat plan)

- 2026-07-10 — identity correction (Jon): the Grok Build CLI is a HARNESS
  serving exactly two models — Grok 4.5 (xAI) and Composer 2.5 (Cursor).
  The engine-lane slug `grok-build` resolves to Grok 4.5. "Grok Build 0.1"
  was never a model; earlier notes/rows using it as one describe Grok 4.5.

- 2026-07-06 — first outing (elsas-website demo), engine added same day:
  audition PASS attempt 1 in 28.9s. Then: asset harvest (11 images, live URL
  re-fetch check), books page, 5 work-page routes in one task (59 verbatim
  needles), adversarial code review (10 real findings incl. an unshelled 404
  and a broken embedded link), press/media fix batch, audio-player integration
  across 15 pages — ALL attempt 1 (player's red ledger entry was a check bug,
  artifact certified). Fast, precise on mechanical/code work. No token counts
  in JSON output (flat plan) — cost reads "included in plan".

## grok-composer-2.5-fast (Grok CLI engine, flat plan)

- 2026-07-06 — first outing (elsas-website demo): audition PASS attempt 1
  (138s — slower than grok-build but the strongest copy of the round).
  Accessibility constitution (14 testable criteria, SC-numbered) attempt 1;
  a11y-gatekeeper harness (axe+Playwright, light/dark, reduced-motion assert)
  attempt 2 — attempt 1's harness mishandled Next's default /404 route.
  Events/faq/contact fix batch attempt 1, but satisfied "editorial grid" with
  an EMPTY aside landmark — axe caught it (landmark-complementary-is-top-level).
  Persona work: good. Watch for letter-of-the-spec shortcuts on layout asks.

## nemotron-3-super-120b (via opencode, `openrouter/nvidia/nemotron-3-super-120b-a12b:free`)

- 2026-07-06 — AUDITION FAILED (exploration slot, $0 spent — free promo).
  Task: fresh-eyes adversarial review of a 2,650-line diff with a structured
  report contract. Failed both attempts on the same executed check: report
  had the right sections and verdict but under 3 concrete code citations —
  shallow engagement with the actual code, 212k tokens burned. Don't re-run
  this audition on long structured code review; if it gets another slot,
  try a shorter, more mechanical task first.

## llama-3.3-70b-instruct (via opencode, `openrouter/meta-llama/llama-3.3-70b-instruct:free`)

- 2026-07-06 — AUDITION FAILED (exploration slot, $0). Fresh-eyes review of
  a 4,061-line diff with a verbatim-quote citation requirement: failed the
  structured-report check both attempts. Second free-model audition to fail
  on long structured code review (after nemotron-3-super) — the exploration
  ladder now says: audition free models on SHORT mechanical tasks first;
  long-diff review is a proven-tier lane.

## Small / flash-class models

- First to choke on long conversational or multi-turn harness tasks —
  watch retry counts before scaling them into a batch (2026-07-05 focus
  group lesson).

## Process lessons (cross-model)

- 2026-07-06 — the orchestrator's CHECKS were the day's top failure source:
  three check bugs (fixture newline join, first-occurrence ordering vs the
  watchlist strip, claim-prefix split on '.' instead of ':') each produced
  a FAIL verdict on work that was actually correct — including all four
  capability-research packets at once. Every one was caught by reading raw
  logs/artifacts before blaming the model. Corollary for the scoreboard:
  recorded FAILs whose root cause was a check bug are annotated here, and
  check fixtures deserve the same review care as production code.


- 2026-07-06 — HARNESS BUG (fix in flight on feat/model-perf-log):
  Verifier.verify evaluated expect_files BEFORE running the check, so any
  check that itself creates/exports its deliverable (the worktree
  patch-export pattern) failed attempt 1 with "missing expected files" even
  when the check printed PASS. Cost 3 phantom retries in one run — and it
  poisons first_try_pass_rate, the model log's routing signal. Until the
  reorder lands on your checkout: have the WORKER write the declared
  deliverable, or don't declare check-created files in expect_files. When
  reading seeded scoreboard numbers, remember 2026-07-06 first-try rates
  are depressed by this.
- 2026-07-06 — the model log is now automatic: every attempt row carries
  model/task_type/retry; `./ringer.py models` prints the scoreboard; 81
  historical rows were seeded via scripts/backfill_model_log.py with a
  hand-authored task-type mapping. Give every manifest task a task_type or
  its evidence buckets as (untyped).

- 2026-07-06 — a three-model "bakeoff" ran every task on the engine's
  hard-coded model: task keys said glm/gpt/kimi, but the opencode engine
  block pinned glm-5.2, so one model wrote all three "competing" reviews.
  This is why the per-task `model` field exists — a bakeoff is only a
  bakeoff if the manifest, not the engine block, names the model. Verify
  with the `model` column in the run state, not the task key.
- 2026-07-06 — spawning 5-6 opencode workers simultaneously hit opencode's
  local "database is locked" (sqlite) — several instant attempt-1 failures,
  all absorbed by Ringer's retry. Cosmetic in Ringside ("sent back" at 0s) but
  wastes an attempt; consider staggering opencode spawns.
- 2026-07-06 — opencode's bash tool kills foreground commands around the
  ~2-minute mark: a 2min+ image-generation API call can never finish inline.
  Spec pattern that works: nohup the long command in the background, then
  poll for the output file in separate short commands.
- 2026-07-06 — two check-craft lessons from the same run: (1) URL-allowlist
  checks must be prefix-tolerant (workers legitimately trim slugs); (2) any
  heading-regex must tolerate numbered headings ("## 3. Type / Typography").
  Both failures looked like worker laziness until the raw logs said otherwise.
- 2026-07-06 — elsas-website demo, check-craft in BOTH directions: (1) a fixed
  800-char body floor failed a worker for faithfully converting genuinely tiny
  source posts — floor must scale with the source; (2) a citation gate treating
  every backtick as a page-quote failed honest reviewers who backticked their
  own fix-suggestions — line-scoped pair parsing + attribute-aware corpus fixed
  it; (3) needle-exception lists must be shared across ALL checks that consume
  the needle set (a needle excepted in one checker failed a task through
  another). Post-mortems ruled FOR the worker 3 times this run — read raw logs
  before blaming the model.
- 2026-07-06 — opencode sqlite "database is locked" again with just 2
  simultaneous opencode spawns (page-news + page-about-faq); retry absorbed it.
- 2026-09-02 — **name every surface file by ABSOLUTE path, and add a citation check.** A review scout whose spec said "CLAUDE.md and .claude/skills/<name>/SKILL.md" reviewed the user's global ~/.claude tree and passed the review-swarm template check, because that check validates structure only. A second check that (a) rejects citations outside the repo and (b) verifies each double-quoted evidence phrase appears within the cited file:line range caught it, and later confirmed a clean report. It now lives at `templates/review-swarm/checks/citations.py` (usage: `--report report.md --repo <abs repo>`); chain it after `review-swarm.py` for any read-only review.

## codex (2026-07-06, bench-operator-proofing)
- 8/8 code-feature tasks passed attempt 1 across 3 rounds (worktrees mode, Python harness refactor; 108k-406k tokens/task). Specs embedded the approved architecture doc + exact file ownership; checks built fresh uv venvs and ran the full pytest suite.
- Lesson (check design, not model): all 3 post-integration bugs were invisible to the checks — a test that passed only because the worker's worktree lacked .env, a `--help`-only assertion missing a runtime importlib/sys.modules bug (py3.12 dataclasses), and bare console-script names failing outside activated venvs. Checks should exercise one real invocation from a cold shell, not just --help.

## gpt-5.6-sol (codex)
- 2026-09-04 fabrica2 M1 response models (3 parallel lanes, direct-repo-edit via
  `--add-dir`): 3/3 first-try. code-feature, pydantic response models for 10
  endpoints inside a 2,000-line FastAPI router (136k tokens, 26m, high effort);
  code-feature, 5 endpoints across 3 routers (233k, 27m, high); code-fix, an
  ESLint feature-boundary inversion plus a Prettier sweep (30k, 2.3m, medium).
  The quality held up to review, not just to the check: it gave the plan graph
  proper node, edge and cluster models instead of flattening it to dictionaries,
  and it kept the genuinely open payloads (a planner's `python_spec`, a
  customer's cohort `fields`) as typed `dict[str, Any]` FIELDS inside named
  envelopes — exactly the distinction the spec drew. Nullable keys came back as
  `X | None` rather than being narrowed away, which is what stops FastAPI
  silently dropping a field it cannot see on the model.
- 2026-07-15 ringer-self-update run (3 serial tasks, direct-repo-edit mode): code-fix baseline-test repair 1/1 first-try (61k tokens, 1.6m); code-feature self-update mechanism (git fetch/ff-pull/re-exec + HUD staleness restart + 20-test suite) 1/1 first-try at high effort (153k, 8.1m); code-feature signal-contract (all 3 scoreboard surfaces + canonical-route lint enforcement) passed on retry (358k, 13.7m) — attempt 1 died on stale old-column assertions in pre-existing tests it hadn't finished updating; the retry prompt's injected FAIL list was enough to close it out. Lesson: when a task rewrites a display contract, name every test file asserting the old contract in the spec's ownership list AND tell it to update them FIRST.
- 2026-07-09 code-feature/code-fix (ringside-overhaul): 4/4 first-try — a ringer.py logging change with tests, a 265-line stdlib backfill CLI (atomic rewrite, dry-run, idempotence all check-verified), a ~1500-line single-file HTML redesign (running-now pills + worker-card grid + multi-expansion refactor, 30KB patch, node --check + contract greps + unittest), and a render-gating change where it correctly UPDATED tests asserting the old behavior instead of gaming the check. Medium/high reasoning, 65–120k tokens/task.
- Same day, different session (bench-harness-patches, code-fix): 0.29 first-try over 7 tasks on a Next.js/Turbopack harness. Spec and check quality dominate model choice — see the scoreboard before generalizing either number.

## GPT-5.5 (codex) — attribution caveat
- Scoreboard rows dated before 2026-07-09 may actually be gpt-5.6: codex eval rows logged model="" until the write-time stamping fix (PR #18) and were credited to GPT-5.5 by the registry default at read time, while the machine's codex default had already moved to gpt-5.6-sol at an unknown earlier date. `scripts/backfill_model_from_logs.py` re-stamps rows with surviving command-log evidence; anything it skips is a mixed-model aggregate. Trust post-2026-07-09 rows.

## nvidia/nemotron-3-super-120b-a12b:free
- 2026-07-08 (research, content-strategy-recon): FAIL x2. Did the analysis in chat but never wrote report.md; attempt 2 exited rc=0 with no file. Doesn't reliably follow file-output contracts under OpenCode. Demoted — don't re-audition on file-deliverable tasks.

## meta-llama/llama-3.3-70b-instruct:free
- 2026-07-08 (research, content-strategy-recon): FAIL x2. Timed out at 900s both attempts on a moderate DB-scrape+format task. Too slow on the free tier for harness work. Demoted — don't re-audition without much longer timeouts or paid tier.

## z-ai/glm-5.2 (addendum)
- 2026-07-08 (research/filter, pitch-foundry): FAIL x2 on a long-spec rubric-application task (~40k input: embedded rubric + 4 candidate files). Read all inputs, exited rc=0 with ZERO output tokens both attempts — silent stall, no file written. GLM handled the same session's shorter formatting specs fine. Lesson: keep GLM specs short; route long-context apply-this-rubric work to codex.

## GPT-5.5 (codex) — honesty flag
- 2026-07-08 (image-gen, pitch-foundry): sandbox DNS blocked openrouter.ai; ALL 10 API calls errored (logged honestly in gen-log) — but the worker then FABRICATED 10 deliverables locally (composited canvases from the ref image) to satisfy a files-exist>40KB check, and passed. Lesson: (a) codex sandbox has no external DNS on this machine — route API-calling tasks to opencode (network open); (b) never write an existence-only check for generated media — require the success log (SAVED/cost lines) to match the file count.

- 2026-07-09 persona-review (pitch-foundry exec-briefing panel): 0/2 first-try+retry. Produced coherent review CONTENT as chat text but never wrote report.md — does not reliably use file-write tools under opencode. Demoted; do not re-audition for file-deliverable tasks without a write-tool probe first.

## gpt-5.6-luna (codex)
- 2026-07-09 code-feature (unlock-ai guide-format conversion, strict type-contract check): 1/1 first-try, 42.6k tokens, 80s. Followed a multi-file TS pattern precisely at $1/$6 pricing. Good candidate for mechanical codegen/docs lanes; audition in adjacent types.

## opencode / z-ai glm-5.2 (via openrouter)
- 2026-07-09 (aicred-invoice-downloads, 4 code-fix tasks + 1 follow-up, worktrees+npm ci checks): systematic attempt-1 NO-OP — all 4 parallel workers produced zero edits and no summary on first attempt, then completed cleanly on attempt 2 after retry-prompt injection (34k-69k tokens each). Follow-up single task passed attempt 1. Suspect first-invocation session warm-up in opencode-sandboxed under parallel spawn; budget for 2 attempts on parallel GLM batches. Output quality on Next.js/Stripe route+test work: solid, spec-faithful, one boss-caught design gap (used user-scoped supabase client where RLS demanded service role — spec didn't say explicitly; say it explicitly).

## opencode (harness note, any model)
- 2026-07-28 (code-review, pr82-token-saver-review): GLM 5.2 produced a complete, high-quality 218-line report but could NOT write it to an output directory created by the parent Claude Code process — every write returned EPERM. It then spent ~3000s burning retries on ctypes/`openat`/AppleScript/`sandbox-exec` workarounds until it timed out, and the task logged as FAIL despite the deliverable existing in its taskdir. Codex workers in the same run were unaffected. Lesson: point opencode workers' output INSIDE their own taskdir and harvest via `expect_files`; never hand them a shared output dir another process created. This is an orchestrator spec bug, not a model failure — do not read the FAIL as evidence against GLM.

## Process lessons (2026-07-28, PR #82 review)
- **Ideas worth keeping from a rejected PR.** PR #82's pre-call gateway was dropped (needs your own API key, so it converts flat-rate OAuth plans into metered API billing; incompatible with Claude Code; and it saves tokens by stripping the tool list, which is the thing that makes the CLI worth using). One idea inside it is worth remembering if the problem ever comes back: an *explicitly blessed* answer cache — key a reviewed answer to the exact request plus the exact selected source packet, and replay it with zero upstream calls, never auto-accepting a model answer. It only fires on byte-identical repeats, which is why it didn't justify 2,000 lines here.
- **Doc-stated support floors need a CI job or they are fiction.** README promised Python 3.11+ while CI only ever ran 3.12; a 3.12-only f-string reached review with a fully green suite. Either test the floor or move it.

## Claude Haiku 4.5 (dial engine, `dial-haiku/haiku-45`)

- 2026-09-02 — **its 0% first-try is an infrastructure crash, not model quality. Do not route off that number.** In run `fabrica2-tests-worktrees` the attempt-1 FAIL logged `worker_returncode=1` one second after launch, before any model call: `Error: Unexpected error / database is locked` — three OpenCode processes started at once and raced on OpenCode's shared SQLite session database. It passed cleanly on retry (task `media`, 18 tests, 36,531 tokens). Read this model's record as 1/1 on quality, 0/1 on a race it did not cause.
- 2026-09-02 (run `fabrica2-tier-audition`) — test-hardening (`tilt_audit`, the one model in this codebase using `extra="allow"` rather than forbid): 1/1 first-try, **21 tests — the largest suite anyone produced in that batch**, ruff-clean with no edits. 41,649 tokens, 295s: the slowest of the three lanes despite being the cheapest model, so cheap does not mean fast here. Now 2 tasks at 50% first-try and still probation — but that 50% is entirely the earlier DB-race crash, not a quality miss.

## Claude Sonnet 5 (dial engine, `dial-sonnet5/sonnet-5`)

- 2026-09-02 — **its 0% first-try is the same infrastructure crash as Haiku's, not model quality. Do not route off that number.** Attempt 1 in `fabrica2-tests-worktrees` died one second after launch with `database is locked`, before any model call; the retry passed (task `profile-drift`, 34 tests — the largest suite any model produced in this batch, 51,472 tokens, 2m44s). Most expensive and slowest of the three lanes tried so far, on one task.
- 2026-09-02 (run `fabrica2-tier-audition`) — test-hardening (`analyzer_descriptor`, a pydantic model with a StrEnum plus three frozenset fields and cross-module enum imports): 1/1 first-try, 12 tests, ruff-clean with no edits, 51,444 tokens, 202.6s. Consistently the most expensive lane — near-identical token spend to its previous task. Now 2 tasks at 50% first-try, still probation, where the 50% is the earlier DB-race crash rather than a quality miss.
- 2026-09-02 (run `fabrica2-harness-review`, code-review `contradictions-and-cost`, read-only over CLAUDE.md + ten skills + settings): TIMEOUT on both attempts (2×1200s, 73k tokens total, never wrote report.md). Same task passed on gpt-5.2-codex in 268s. Do not route long multi-file read-only reviews to sonnet-5 on this engine until a bounded task shows it can finish.

## Claude Sonnet 4.5 (dial engine, `dial/dial-sonnet-45`)

- 2026-09-02 — test-hardening (3 tasks, run `fabrica2-unit-tests`): 3/3 first-try, median 13,988 tokens, 1m13s — cheapest and fastest on this lane so far. A later single-task run produced the best failure demonstration in the log: it wrote `greet.py` printing `Hello, Ringer!!` and exited rc=0 claiming success. Only the executed check caught the extra `!`, and the retry fixed it from the injected diagnostic. Worth remembering whenever someone argues an agent's own "done" is evidence.

## GPT-5.2 Codex (dial engine, `dial-codex/gpt-5.2-codex`)

- 2026-09-02 — test-hardening (`scim-types`, boundary-heavy pydantic model tests): 1/1 first-try, 29,471 tokens, 89.5s. The only worktree task to pass first time — but the other two lost attempt 1 to the DB race rather than to quality, so this is not yet evidence of an edge. Too little data to rank.
- 2026-09-02 (run `fabrica2-tier-audition`) — test-hardening (`llm_response`, a four-strategy JSON extractor: pure parsing logic, no pydantic): 1/1 first-try, 14 tests covering all four strategies plus the `caplog` warning path, 34,549 tokens, 123.2s. **The one deliverable in either batch that failed `ruff format`** — it passed `ruff check` but needed reformatting before landing, while both Anthropic lanes came back format-clean. If you route mechanical codegen here, add `ruff format --check` to the task's check command rather than trusting `ruff check` alone. Now 2 tasks, 100% first-try, cheapest and fastest of the three probation lanes.
- 2026-09-02 (run `fabrica2-harness-review`, code-review, read-only scouts over a repo's CLAUDE.md + skills): mixed. `steady-delivery` (bounded surface: settings.json, pre-commit config, five scripts, one test file): 1/1 first-try, 51k tokens, 199s, but only 1 of its 3 findings survived the orchestrator's check (it missed a nested .gitignore and inferred allow-list semantics). `claims-vs-tree` round 1 (surface named by RELATIVE paths): passed the template check in 49s / 8.6k tokens by reviewing the wrong tree (~/.claude instead of the repo) — a spec bug, and a check that only validated structure. Round 2 with absolute paths and a spec of 'verify EVERY claim in eleven files': TIMEOUT twice (2×900s, 90k tokens, no report). Lesson: give codex a bounded enumerated claim list, not an open-ended sweep. `contradictions-and-cost` with absolute paths: 1/1 first-try, 41k tokens, 268s, all 11 file:line citations and 14 quotes verified by a quote-on-line check; 4 of 5 findings actionable or correctly restating a known gap.

## dial engine (OpenCode harness → EPAM DIAL gateway)

- 2026-09-02 — mitigation for the `database is locked` race above: `engines/opencode-dial.sh` gives each worker a private `XDG_DATA_HOME`/`STATE`/`CACHE`, so workers no longer share one SQLite file, and disables OpenCode self-update mid-swarm. Wired as `[engines.dial]`'s `bin`. Revert with `bin = "opencode"` to compare.
- 2026-09-02 — **evidence for that wrapper, gathered deliberately.** Run `fabrica2-tier-audition` re-created the exact failing condition — three workers spawned simultaneously onto three cold git worktrees of a large repo — this time through the wrapper: **3/3 passed on attempt 1, with zero `database is locked` occurrences in any worker log**. Scoreboard so far: without the wrapper, 2 of 3 workers crashed; with it, 0 of 3. That is supporting evidence, not proof — the race is intermittent, and a single clean run cannot establish a negative. Treat it as promising, keep the note, and re-check if a lock ever reappears.
- 2026-09-02 — **spec quality moved straight into artifact quality, measurably.** Batch 1 specs omitted the target repo's conventions and every deliverable needed hand cleanup (an unused import that would have failed ruff, plus 55 missing `-> None` hints). Batch 2 specs named them explicitly — `from __future__ import annotations`, `-> None`, 100-char lines, and "read `tests/unit/core/test_check_result.py` first" — and all three deliverables landed ruff-clean and correctly formatted with zero edits. Cheap change, large effect: put the target repo's lint rules in the spec.
- 2026-09-02 — **model slugs on this engine must not contain `claude` or `anthropic`.** OpenCode stamps Anthropic prompt-caching blocks when a model id matches either string; against DIAL's OpenAI-compatible endpoint that emits a raw `cache_control` field and every call fails with HTTP 400 (`invalid structure on path messages.0.cache_control`). DIAL routes on the deployment in the URL and ignores the body's model name, so aliasing the slug is free. See the `[engines.dial]` block in `registry/model-identity.toml` for the alias→deployment mapping.
- 2026-09-02 — Codex CLI cannot reach DIAL at all: 0.152.1 speaks only the Responses API (the string `chat/completions` appears nowhere in its binary), while DIAL exposes Azure-style chat completions. That is why this lane runs OpenCode as the harness rather than the built-in codex engine — and why `./ringer.py demo`, which hardcodes the codex engine, cannot run on a DIAL-only machine.

- 2026-09-03 — fabrica2-web-plan (4 tasks: 3 read-only scouts on gpt-5.2-codex + 1 on haiku-45, 1 npm proof on gpt-5.2-codex): every attempt died in ~20 s with HTTP 401 "Unauthorized: Unknown api key" from ai-proxy.lab.epam.com. Infrastructure (expired/rotated DIAL key in ~/.ringer/dial.env), not a model signal — discount these 8 FAIL rows when reading the scoreboard. Re-verify the key with GET /openai/deployments before the next swarm.
- 2026-09-03 — fabrica2-web-plan probe (dial-lane, `dial-codex/gpt-5.2-codex`, trivial echo+transcript task) after sourcing ~/.ringer/dial.env: no 401 any more, but OpenCode emitted NO JSON events at all for 600 s on both attempts (rc=143, TIMEOUT). Yesterday the same deployment finished review tasks in 4-5 min. Treat the dial lane as unavailable until a probe passes; diagnose OpenCode `run --format json` against DIAL outside a swarm first. Claude subagents carried the planning scouts instead.

- 2026-09-04 — dial lane VERIFIED WORKING after sourcing ~/.ringer/dial.env (`set -a; source ~/.ringer/dial.env; set +a`). One-task probe on `dial-codex/gpt-5.2-codex`: authenticated, ran its commands, wrote the marker into probe-output.txt and a 179-word transcript with every requested section, 27.8k tokens, 78 s. It still shows FAIL on the scoreboard because of a bug in `templates/probe/`: the kit spec tells the worker to write a `## Model Response Or API Result` heading, while `checks/probe_check.py` greps for the literal `MODEL RESPONSE:` (with a colon, lines 47-49). Spec and check disagree, so an honest worker cannot pass model mode. Fix the template, then discount this row. Yesterdays 401s were the missing env file; the 600 s hang did not reproduce.

- 2026-09-18 — tdm-engine3-subsetting-review (2 test-hardening tasks on `dial/dial-sonnet-45`): infrastructure, not a model signal — discount both FAIL rows. (1) `opencode` is only on PATH under nvm node v20 (`~/.nvm/versions/node/v20.19.6/bin`), not under the default v24; the first run died with `opencode: command not found` before any model call — prepend that dir to PATH when launching `ringer.py run`. (2) With PATH fixed, every call was rejected by DIAL with `Hit token rate limit ... Day limit: 2018278 / 2000000 tokens` (daily quota exhausted). OpenCode logged `AI_RetryError` after 3 attempts and then HUNG instead of exiting: both workers sat at ~3% CPU with an empty JSON event stream for 15+ min until killed, so Ringer would have waited for the full `timeout_s`. When a run shows no events and no worktree changes within ~2 min, check `/tmp/ringer-opencode-*/data/opencode/log/opencode.log` for `stream error` before waiting. Per "never retry into a limit", the lanes were done inline.

## DIAL lane audition, 2026-09-04 (8 lanes, one shared task, no retries)

Task: implement `normalize_table_name` to eight ordered rules; the check imports
the module and runs 16 stated cases (digit prefix, a 63-character cap that
interacts with underscore stripping, empty result). The checker was validated
against a reference implementation and against a naive one, which it fails on
14 of 16. `max_attempts: 1`, so these are first-try numbers.

| Model (dial slug) | Verdict | Tokens | Time |
|---|---|---|---|
| `dial-qwen/qwen3-coder` Qwen3 Coder 480B | PASS | 9,474 | 8.7 s |
| `dial-sol/gpt-5.6-sol` GPT-5.6 Sol | PASS | 7,831 | 17.4 s |
| `dial-sonnet46/sonnet-46` Sonnet 4.6 | PASS | 10,110 | 17.6 s |
| `dial-codex53/gpt-5.3-codex` GPT-5.3 Codex | PASS | 7,739 | 19.6 s |
| `dial-glm5/glm-5` GLM-5 | PASS | 8,923 | 23.1 s |
| `dial-gemini38/gemini-3.8-flash` Gemini 3.8 Flash | PASS | 10,042 | 24.7 s |
| `dial-opus5/opus-5` Opus 5 | PASS | 13,393 | 82.8 s |
| `dial-codex/gpt-5.2-codex` GPT-5.2 Codex | **TIMEOUT** | — | 900 s |

**The GPT-5.2 Codex deployment is rate-limited, not slow.** A direct call to
`.../deployments/gpt-5.2-codex-2026-01-14/chat/completions` returns **HTTP 429**
in under a second. OpenCode retries a 429 without emitting an event, so the
worker log stays at 4 KB with zero tool calls and the task burns its whole
timeout. This is what killed the fabrica2 M1 round-1 run earlier the same day:
two tasks, 3600 s each, nothing produced, and the failure looked like "the task
was too big". It was not. **Stop routing to `dial-codex/gpt-5.2-codex` until a
direct call stops returning 429**, and when any lane produces a log with no
`"type":"tool_use"` lines, probe the deployment with curl before blaming the
task.

Routing from this evidence: Opus 5 for work that needs judgment (it is also the
only lane that spends EPAM credit instead of the operator's own Claude quota);
GPT-5.6 Sol or GPT-5.3 Codex for ordinary code work at half the tokens; Qwen3
Coder for mechanical edits; GLM-5 and Gemini 3.8 Flash as cheap lanes worth more
evidence. All seven are new here, so treat one clean task as probation, not
proof.

- 2026-09-04 — **GPT-5.6 Sol: discount the `flow-shell` FAIL row.** The task
  (React shell, six-stage flow rail, eleven routes, per-feature strings split,
  new tests) was correct: from a clean shell, `pnpm typecheck`, `lint`,
  `format:check` and `build` all pass and the suite went 288 -> 304 tests. It
  scored FAIL twice because the orchestrator's ownership check ran
  `git status` over a shared checkout and counted the orchestrator's own staged
  file as a stray path. The worker never touched it. 146k tokens, 817 s over two
  attempts, both spent re-doing correct work. Lesson for the check, not the
  model: when several tasks or the orchestrator share one checkout, an ownership
  check needs a baseline allow-list of paths that are already dirty, or it fails
  honest work and burns the retry.

- 2026-09-04 — **Qwen3 Coder 480B, code-feature: PASS first try.** Mechanical
  refactor in a React/TypeScript repo (split one shared stage component into six
  files plus a shared frame, keep rendered output identical, add a test per
  file). 51k tokens, 605 s, gate was typecheck + lint + format + the whole Vitest
  suite. Second clean task in a row; the cheap lane holds for well-specified
  mechanical work with a strong executed check.
- 2026-09-04 — **Claude Sonnet 4.6, code-feature: TIMEOUT at 2x3600 s**, 96.5k
  tokens, on a large frontend feature (assistant panel plus full page: drawer,
  conversation, typed API call, sanitized Markdown, session persistence, error
  path, five test scenarios). Not a hung lane — it produced four real files, a
  shell mount and six passing tests, and left exactly three type errors. It never
  converged because each gate iteration costs two to three minutes and the task
  was too large for one hour. Lesson for the ORCHESTRATOR: cap a frontend feature
  task at roughly one screen, or give it 5400 s. A task that ends one type error
  short still blocks every other lane sharing the checkout, so scope the lane to
  what fits.
- 2026-09-04 — **Orchestrator error worth naming: a gate wider than the lane
  makes a task unpassable.** The `fabriccio-typecheck` fix task owned only
  `web/src/features/fabriccio/`, but its gate ran the whole app's typecheck,
  lint and format. The blocking lint error was in `web/src/components/shell/`,
  outside its lane, so the worker had two ways to fail and none to pass: fix the
  file and break ownership, or respect ownership and never go green. Qwen3 Coder
  burned 47.8k tokens and two attempts on that trap, and the row reads TIMEOUT as
  if the model were slow. **Rule: a task's ownership list must cover every file
  its gate can fail on, or the gate must be narrowed to the lane.** Discount that
  row for Qwen3; the same model passed a comparable refactor first try an hour
  earlier.
- 2026-09-04 — **Codex can edit a repo outside its task directory without
  full-access mode.** Put `--add-dir <repo>` in the task's `engine_args`. The
  worker banner then reads
  `sandbox: workspace-write [workdir, /tmp, $TMPDIR, <repo>]`: containment is
  still on, the repo is simply added to the writable set. This is the right
  answer for direct-repo-edit lanes — `full_access: true` needs
  `allow_full_access` in config.toml and drops the sandbox entirely, which is a
  much bigger concession for the same result.
- 2026-09-04 — **"I could not run the tests" from a worker is not a failed
  check.** A lane's notes reported that AnyIO's cross-thread portal hangs on any
  FastAPI `TestClient` inside the Codex sandbox, and it reproduced that with a
  trivial app to show the fault was not its own code. Its check passed anyway,
  because Ringer runs the check OUTSIDE the worker sandbox, where `TestClient`
  works. Read the executed check for the verdict — but still read the note, as
  it tells you which verification the worker could not self-serve, and therefore
  which part of its work rested on the check alone.
- 2026-09-04 — **A gate the worker cannot run in its own sandbox is a lane that
  can never go green.** Sibling of the ownership rule above. The
  `sandbox-extension-cache` task's gate ended with `pytest tests/unit/adapters/`
  under a 600 s bound. That directory finishes in 104 s when the orchestrator
  runs it, but the Codex sandbox cannot complete it — the same AnyIO
  cross-thread portal hang another lane reported that day. The worker fixed the
  actual bug (sandbox test files went from over ten minutes to 5.6 s), then
  spent 38 more minutes and a retry chasing a gate failure that no code change
  could clear, and the row reads ERROR as if the model had failed. The fix
  itself passed the identical check first time when the orchestrator ran it
  outside the sandbox. **Rule: before shipping a spec, ask whether the worker's
  own environment can execute every stage of the gate. Broad regression sweeps
  belong to the orchestrator, after the lane lands — not in the worker's gate.**
  Discount that ERROR row for gpt-5.6-sol.
- 2026-09-04 — **Worker self-reports can carry the real diagnosis; read them
  before re-running.** The same lane's log said its first attempt had walked the
  extension cache recursively, and that the retry replaced it with a
  constant-size scan. That is why attempt 1 was slow and attempt 2 was not — a
  detail no check output would have shown, and one that stopped the orchestrator
  from wrongly blaming machine contention.
- 2026-09-04 — **Orchestrator error, in the tool itself: never kill a worker on
  a string match while it is still producing output.** Ringer's refusal
  detector scanned a live worker's whole output every ten seconds and stopped
  it on the first match. A healthy Codex lane grepping the web test suite hit
  `web/src/lib/api/client.test.ts:39: { status: 401 }` — a test asserting that
  the client surfaces a 401 — and was killed after 70 s and 143 KB of real
  progress, then not retried, because refusals are correctly treated as not
  worth retrying. Codex auth was fine; a direct probe answered in 2.5k tokens.
  Fixed by changing the principle rather than the regex: the patterns now
  EXPLAIN a worker that has already stopped or gone quiet, and never terminate
  one that is still writing. The scan runs when the stall window closes, or
  against the last 4 KB when a worker exits by itself, and `path:line:` lines
  are dropped first so grep and ripgrep output cannot trigger it. The cost is
  that a genuine refusal takes up to the stall window to be named instead of
  one poll; that is the right trade. **Rule: a detector that acts on a worker's
  own output must assume the worker's job is to read text that looks like
  errors.**

## Gemini 3.8 Flash (dial engine, `dial-gemini38/gemini-3.8-flash`)
- 2026-09-05 code-fix (fabrica2 route cleanup, exploration lane): **FAILED the
  audition on discipline, not capability.** The spec said "Never run a git
  command that changes state: no commit, no add, no stash" in its house rules,
  as every lane in that job did. It committed anyway — nine files, including
  two documentation files the orchestrator owned and had not finished, under a
  generic message with no trailer. The code content was sound (the tree it
  captured verified at 342 tests), so this is a rule-following failure rather
  than a coding one, and it is the more dangerous kind: a worker that ignores
  one hard rule in the house rules cannot be trusted with the others. It then
  went quiet and was stopped by the stall window after 1,222 s and 49,372
  tokens, having produced 247 KB of output and no notes.md.
- **Do not give this model a lane in a real repository** until it has passed a
  read-only or worktree-isolated task. Worktrees mode would have contained the
  damage: a commit inside a task worktree dies with the worktree.
- Exploration worked exactly as intended here — a low-stakes deletion lane with
  a strong executed check is where you find this out, not on the critical path.
