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
- 2026-09-10 — code-fix, nexo Run 1 pilot (run `nexo-run1-fix-swarm`, 3 tasks):
  **3/3 pass, 2 first-attempt.** work#666 (bash + a race harness), work#919 (a
  7-file instruction sweep), work#950 (pre-flight + new selftest cases, attempt 2).
  Quality sat above the checks' floor rather than on it: work#666 identified that
  the real fix was reading the head sha ONCE and reusing it — atomicity — not
  merely deriving the diff locally, and work#919 respected its ownership boundary
  by leaving CLAUDE.md untouched while flagging that it needed a follow-up. All
  three patches later merged unchanged.

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
- 2026-09-10 — **an aggregate scoreboard row cannot tell "the model cannot do
  this" from "the harness was broken", and reading it as capability nearly cost a
  24-ticket batch.** glm-5.2's code-fix row read 0.17 first-try / 0.22 pass over 63
  tasks. All 63 came from ONE sibling factory's run whose dominant failure was
  `worker_returncode=1` with `missing_expect_files` — a single block of 64 of 69
  identical. That is a harness signature. The same model then went 3/3 on a
  different factory's pilot; the runs differed in check design and deliverable
  path, not in engine. **Group failures by run_id and read the failure mode before
  routing on the number.**
- 2026-09-10 — **deliverables land in the worktree; the CHECK exports them.** A
  scout task declared its deliverable at an absolute path outside the worker's
  sandbox. The log shows the model finding the correct answer in four tool calls
  and then burning ~40 on `write`, `cat >`, `dd`, `cp`, python, `xattr -c` and
  `touch` against a path it was never allowed to touch. Sibling fix-swarm tasks
  were immune purely because their check exported the patch from the check side.
  A worker can write inside its own worktree and its assigned TMPDIR, nowhere
  else — and it will spend a whole task's budget proving that to you.

## codex (2026-07-06, bench-operator-proofing)
- 8/8 code-feature tasks passed attempt 1 across 3 rounds (worktrees mode, Python harness refactor; 108k-406k tokens/task). Specs embedded the approved architecture doc + exact file ownership; checks built fresh uv venvs and ran the full pytest suite.
- Lesson (check design, not model): all 3 post-integration bugs were invisible to the checks — a test that passed only because the worker's worktree lacked .env, a `--help`-only assertion missing a runtime importlib/sys.modules bug (py3.12 dataclasses), and bare console-script names failing outside activated venvs. Checks should exercise one real invocation from a cold shell, not just --help.

## gpt-5.6-sol (codex)
- 2026-07-15 ringer-self-update run (3 serial tasks, direct-repo-edit mode): code-fix baseline-test repair 1/1 first-try (61k tokens, 1.6m); code-feature self-update mechanism (git fetch/ff-pull/re-exec + HUD staleness restart + 20-test suite) 1/1 first-try at high effort (153k, 8.1m); code-feature signal-contract (all 3 scoreboard surfaces + canonical-route lint enforcement) passed on retry (358k, 13.7m) — attempt 1 died on stale old-column assertions in pre-existing tests it hadn't finished updating; the retry prompt's injected FAIL list was enough to close it out. Lesson: when a task rewrites a display contract, name every test file asserting the old contract in the spec's ownership list AND tell it to update them FIRST.
- 2026-07-09 code-feature/code-fix (ringside-overhaul): 4/4 first-try — a ringer.py logging change with tests, a 265-line stdlib backfill CLI (atomic rewrite, dry-run, idempotence all check-verified), a ~1500-line single-file HTML redesign (running-now pills + worker-card grid + multi-expansion refactor, 30KB patch, node --check + contract greps + unittest), and a render-gating change where it correctly UPDATED tests asserting the old behavior instead of gaming the check. Medium/high reasoning, 65–120k tokens/task.
- Same day, different session (bench-harness-patches, code-fix): 0.29 first-try over 7 tasks on a Next.js/Turbopack harness. Spec and check quality dominate model choice — see the scoreboard before generalizing either number.

## GPT-5.5 (codex) — attribution caveat
- Scoreboard rows dated before 2026-07-09 may actually be gpt-5.6: codex eval rows logged model="" until the write-time stamping fix (PR #18) and were credited to GPT-5.5 by the registry default at read time, while the machine's codex default had already moved to gpt-5.6-sol at an unknown earlier date. `scripts/backfill_model_from_logs.py` re-stamps rows with surviving command-log evidence; anything it skips is a mixed-model aggregate. Trust post-2026-07-09 rows.

## GPT-5.5 (codex) — juce-test, first datapoint
- 2026-08-06 (juce-test, juce-lane-smoke — sombra-audio C++/JUCE lane, first ever run of that lane): 2 attempts, both scored FAIL, **both blameless**. The kit's own check has an inverted anti-vacuity gate (`check_juce_feature.py:98` only parses CTest's *failed*-run phrasing), so a green suite can never pass — see work#454. The worker's output was correct on attempt 1: right Catch2 case, right CMake registration, ownership respected, no commits, substantive notes, full suite 99/99. Do NOT read these two rows as evidence against GPT-5.5 on JUCE work; the scoreboard cannot distinguish them from real failures. Re-run once #454 lands to get a real first datapoint.
- Retry-context caveat observed in the same run: the `Previous attempt failed:` payload carried a fragment of the prior attempt's `notes.md` diff rather than the check's `[FAIL]` line. The worker rewrote an innocent notes line and failed identically — a model given no failure signal cannot self-correct, and the retry row is not a capability signal.

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

### codex (codex-cli 0.137.0-alpha.4) — 2026-07-22, hospedo phase-5c pricing

- `code-feature` x3, `code-fix` x1. 4/4 eventually passing; 3/4 first-try.
- **The one FAIL was my check's bug, not the model's.** `w54-backfill` was marked
  fail after 2 attempts because the gate grepped `\b85\b` for expected rates, and
  C# writes them as decimal literals (`85m`) — no word boundary between a digit
  and `m`, so a correct implementation could never satisfy it. The gate also
  flagged the negative guard (`Assert.DoesNotContain(... 110m or 95m)`) that the
  spec itself had *required*. Work was correct on attempt 1. Read the scoreboard
  accordingly: do not treat this as evidence against codex on code-feature.
- Lesson for future checks: never word-boundary-match numerics in C#; allow an
  optional `[mMdDfF]` suffix. And exempt negative/guard assertions before
  banning a literal.
- Recurring environment note: codex's sandbox cannot bind VSTest's local TCP
  listener (`SocketException (13): Permission denied`), so it can only ever
  self-verify by `dotnet build`, never `dotnet test`. Every run reported
  build-only verification. The executed check (running outside that sandbox) is
  therefore doing all real verification here — do not relax it on the assumption
  the worker ran the suite.

### work#233 model bake-off — 2026-07-25, hospedo payments-05 tier refund (`dotnet-feature`)

Controlled 3-seed comparison on one frozen commit, identical spec/check/timeouts;
only engine+model differed. Full write-up:
`mulhacenlabs-engineering/outputs/engineering-experiments/2026-07-23-model-bakeoff/02-results.md`.

**⚠️ SCOREBOARD CORRECTION — read this before trusting `ringer.py models` for
`dotnet-feature`.** Four rows in the local scoreboard are INFRASTRUCTURE failures
recorded as model failures. Ringer has no `infra_error` concept, so they land as
0.00 pass-rate against the model:

- **GPT-5.5 (codex), 2 failed** — a live OpenAI incident ("Elevated error rates",
  APIs/ChatGPT/Codex; `503 … biscuit_baker_service_me_circuit_open`, 30 and 42
  occurrences). The worker never reached the code. NOT evidence against GPT-5.5.
- **Kimi K2.7 Code, 2 failed** — the gate's asserter script was an UNTRACKED file in
  the monorepo; a concurrent session switched branches and deleted it mid-run. One of
  those runs produced a **correct** implementation (4 passed / 2 skipped / 6 total, all
  three named tier examples green, 610 website tests) and was still marked FAIL.
  Proven: re-running that run's exact check against its preserved worktree exits 0.
  NOT evidence against Kimi.

Real results, measured after the rig was isolated (dedicated worktree pinned at
BASE_SHA + checks owned by the experiment package):

- **`openrouter/z-ai/glm-5.2` — 3/3, all FIRST-TRY.** 434.6 / 662.3 / 837.7s.
  Modified exactly the 4 owned files every time, *including* the Gherkin step
  bindings, and added tests rather than deleting any (609/617/605 vs a 605 floor).
  Implementation quality spot-checked and genuinely correct — cumulative idempotency
  guard, correct tier boundary, no rail call on a 0% tier, honest out-of-scope notes.
  Strong pick for `dotnet-feature`.
- **`openrouter/moonshotai/kimi-k2.7-code` — 1/3, 0 first-try.** Fails the same way
  twice: writes plausible domain logic, never wires it to the acceptance criteria.
  Seed 2 touched 2/4 files (no step bindings); seed 3 touched 1/4 (10 lines).
  Monotonic disengagement (63 → 38 → 25 steps) and missed the stated `notes.md`
  output contract on 2/3 seeds. **Do not route `dotnet-feature` work to it.**

**Token accounting — the configured `token_regex` is wrong by ~20×.**
`token_regex = '"tokens":\{"total":([0-9]+)'` matches the FIRST `step-finish` event
only; opencode emits one per step. Measured: regex 17,677 vs actual 391,051 fresh.
But summing per-step `total` is ALSO wrong — `total = input+output+reasoning+cache.read`,
so the sum is dominated by cache re-reads (2,957,831 vs 391,051 fresh on the same run).
Use `input+output+reasoning`. Extractor: the experiment package's `tools/engine_tokens.py`.

**Cost:** opencode's self-reported cost runs ~34% HIGH ($0.903 claimed vs $0.673
billed) because it prices cache reads at fresh-input rates. Ground truth is
`GET https://openrouter.ai/api/v1/key` → `data.usage`, snapshotted either side of a run.

**Cost variance is a caching artefact, not a model property.** Identical model, task
and prompt across three seeds gave a **1.8× cost spread** ($0.673 → $1.197), driven
entirely by provider-side prompt-cache hit rate (seed 3 got half the cache reads and
double the fresh input). Treat "cost per success" on a single seed as noise.

**Plan-billed vs metered is not comparable.** codex on plan is $0 marginal; GLM at
~$0.89/run is strictly more expensive. The argument for a second lane is availability
and concurrency — proven today when OpenAI went down and the OpenRouter lanes kept
working — not cost.

**Evidence gap:** when a task passes on attempt 2, why attempt 1 failed is
unrecoverable — only the final attempt's `check_output_tail` is stored.

#### work#233 addendum — candidate A measured, 2026-07-25 (same `dotnet-feature` task)

The OpenAI incident cleared, so the baseline arm was re-run in full. **codex / gpt-5.5
(effort medium, plan-billed): 3/3 PASS, all first-try**, 781.1s / 2098.2s / 808.4s
(median 808.4s). $0.00 marginal — proven by a byte-identical OpenRouter `usage` reading
before the first seed and after the last. Supersedes the two outage rows: GPT-5.5 is
**not** 0.00 on this task type.

Final three-way, one frozen commit, identical spec/check/timeouts:

| lane | pass | first-try | median | cost/success |
|---|---|---|---|---|
| codex gpt-5.5 (plan) | 3/3 | 3/3 | 808.4s | $0.00 |
| `openrouter/z-ai/glm-5.2` | 3/3 | 3/3 | 662.3s | $0.894 |
| `openrouter/moonshotai/kimi-k2.7-code` | 1/3 | 0/3 | 925.0s | $1.757 |

**The cheap lane is not cheaper.** codex on plan is $0 marginal, so GLM is strictly more
expensive per success. The defensible reasons for a second lane are availability (the
outage removed codex for a day while OpenRouter kept working), concurrency beyond plan
limits, and scope discipline — GLM touched exactly 4 files on every seed, codex touched
5/6/4 for the same gate outcome.

**⚠️ codex cannot self-verify .NET work, and it costs real wall-clock.** a-2 spent ~22 of
its 35 minutes sitting through five-minute MSBuild timeouts inside its own sandbox
("*the command is still alive and repeating the same sandbox MSBuild failure*"). Same
hang recorded on the 2026-07-23 run, and consistent with the VSTest-listener note above.
It still passed 3/3 — because Ringer's check runs UNSANDBOXED and did the real
verification. Consequences: (1) do not relax an executed check assuming the worker ran
the suite — for codex it demonstrably did not; (2) roughly half of GLM's *mean* speed
advantage is this defect, not model speed (medians: 662s vs 808s, ~18%; means: 645s vs
1229s, ~48%). Do not encode the mean into any routing rule.

**codex token capture is unusable.** The engine's `token_regex` matched nothing at all in
today's logs, yet Ringer still recorded `tokens: 99 / 144 / 143`. Those numbers are not
traceable to any worker output. Treat codex token counts on the scoreboard as noise.

## nvidia/nemotron-3-ultra-550b-a55b:free (OpenCode / OpenRouter)

- **2026-08-31 — code-review (claim verification), nexo.** First outing, exploration lane in a
  5-task batch. **Passed first try**, and was the *fastest* task in the run at 92s against Codex's
  118–236s. 32,512 tokens (Codex tasks in the same run reported 38–93). Task was the most
  mechanical of the five — cross-check five `.graphql` selection sets against the `.dart` screens
  that consume them — and it handled the executed check cleanly: five verdicts, every one citing
  a real `file:line`, all of which I verified independently afterwards (including a schema line
  number I had not looked up myself). One nit: it emitted line *ranges* (`file.dart:51-60`) where
  the contract asked for `file:line`; the validator's regex tolerated it, a stricter one would
  have failed honest work. Free, 1M context. **Worth another audition on mechanical
  cross-reference work** — no evidence yet on tasks needing judgement rather than lookup.

### 2026-09-01 — three-lab panel on a merge/gate judgement call (task_type code-review)

- **Nemotron 3 Ultra (openrouter/nvidia/...:free)** — judgement task, 2 attempts. Attempt 1 died on
  an upstream `502 Service temporarily overloaded` from NVIDIA *mid-reasoning* (the log shows it had
  reached "Let me write the report" before the provider dropped it). Attempt 2 passed, 281s — by far
  the slowest of the three. Verdict quality was good: it named the mechanism, took the harder line on
  the release question and gave a precedent-creep argument. Worth keeping in the free-exploration
  slot for judgement work, but not for anything time-critical: a free NVIDIA endpoint that 502s under
  load will do it again.
- **GLM 5.2 (openrouter/z-ai/glm-5.2)** — same task, and the standout. It produced the one insight no
  other panellist or the orchestrator had: that a sanitizer exclusion *rots*, and the defence is
  making it self-expiring (a `TODO(ticket)` a mechanical check fails on once the ticket closes). It
  also asked the single question that could have flipped the whole answer — whether the racy path was
  reachable from host automation rather than only a user action. Cheap, fast (53s), and it reasons
  about second-order consequences rather than restating the brief.
- **GPT-5.5 · high (Codex)** — passed first try, 55s, 16 tokens of report. Tightest of the three and
  the only one to name the *psychological* cost of the recommendation ("landing T3 may make the race
  feel handled because CI is green"). Reliable default for this shape of question.

⚠️ **Orchestrator error worth recording, not a model failure.** The first run failed 3/3 because the
manifest set a per-task `"workdir"`. Ringer ignores it — the taskdir is `manifest.workdir / task.key`
— so every worker wrote to `<workdir>/<key>/` while every check read `<workdir>/<name>/`. The GLM
worker diagnosed this correctly from inside its sandbox and said so in its output; it was right, and
verifying it on disk took one `ls`. If a task key must match a directory name, do not put a colon in
the key.

### z-ai/glm-5.2:free

- **2026-09-03 · code-fix (shell) · NO-OP TWICE, task failed.** Audition on monorepo
  round 2, work#470 (rewrite `scripts/smoke-photo-upload.sh` off four deleted routes and
  add a companion tests script). Both attempts left `git status` clean — the ownership
  guard's "the worker changed NOTHING" branch, on attempt 1 *and* on the retry that
  injected that exact message. Spec was 7 KB with the four historic traps, the four dead
  routes and an in-repo precedent named (`ses-permission-args-tests.sh`); every other lane
  in the same run on codex/gpt-5.5 produced real diffs from comparably sized specs, so
  this is not a spec-length problem. Ended the audition — re-run the lane on codex.
  Cost of the experiment: one lane, zero tokens billed (free model), ~2 wasted slots.
  ⚠️ **Evidence caveat, added the same day:** the raw worker log was overwritten when that
  lane was re-run on codex at the same path, so what survives is the run-state record
  (`monorepo-round2-...json`: status fail, "the worker changed NOTHING"), not the raw
  transcript. The opencode engine block WAS verified configured and uncommented
  (config.toml:183, `model_default = openrouter/z-ai/glm-5.2`), so this is not a
  misconfiguration artefact. Treat it as one solid observation rather than a settled
  verdict — re-audition before writing the model off.

## Bakeoff 2026-09-03 — cheap lens review vs Codex (nexo PR#248 fixture)

Fixture: the first state of nexo PR#248, an 837-line .NET seeder diff whose real defects are
known from seven Codex passes. Scored on whether each model recovered them.

- **qwen/qwen3.8-flash** — code-review. Found BOTH known defects **plus a real latent bug seven
  Codex passes missed**: a shared scoped DbContext where a fixture throwing after `AddAsync`
  leaves a half-built entity that the next fixture's `SaveChangesAsync` commits. Verified against
  the code and fixed. ~$0.004, 43k tokens. TIMEOUT at 1800s — not slow reasoning: it wrote a
  complete report in minutes then kept re-verifying the file. Adding an explicit "stop once
  report.md is written" line to the spec is the fix; do that for any flash-class reviewer.
- **deepseek/deepseek-v4-flash** — code-review. **PASS on first attempt**, 204s, 36k tokens,
  ~$0.003. Recovered both known defects and independently raised directory-visibility-forced-
  public as P1. Best cost/quality/reliability of the field. Undated slug only.
- **deepseek/deepseek-v4-pro** — code-review. FAIL, 2 attempts, 48k tokens. Found strictly LESS
  than its cheaper Flash sibling and failed the contract on `finding_citation_unresolvable` —
  cited `DemoContentSeeder.cs:86` etc. when only the diff was staged. **The bigger DeepSeek was
  worse than the smaller one here**; do not assume Pro > Flash for review work.
- **z-ai/glm-5.3 / glm-5.3-flash** — not routable on this account: instant `UnknownError:
  Unexpected server error`, 0 tokens, both attempts. Only `z-ai/glm-5.2` works. Not a quality
  result.

⚠️ **Dated OpenRouter slugs failed across the board** (`-0731`, `-0813`): instant server error,
0 tokens. Undated slugs worked. Use undated.

Context: this bakeoff was run because Codex quota was exhausted after ~880k tokens / EUR20 on
that single PR. Four cheap-model reviews of the same diff cost about a cent in total.

### Validation round, same day — the first bakeoff was too easy, and the conclusion flipped

Two new fixtures on the same PR: **recall** (a state containing the subtle mirrored-`musicLinks`
test — a green test that re-implemented the handler it claimed to verify, which Codex found) and
**precision** (final state, all known defects fixed — does the model invent findings?).

- **deepseek/deepseek-v4-flash** — precision **PASS**: correctly reported "no defects found" on
  clean code. Recall **FAIL**: it did not find the mirrored test at all, concluding "no verified
  defect found", offering only a speculative P3 null-`SequenceEqual` observation at *low*
  confidence. 111k tokens over 2 attempts, and it still failed the contract on an unresolvable
  citation (`DemoContentSeederTests.cs:708` — past the end of a 323-line file). Its report shows
  coherence decay on long context: `SetEpkConent`, `UpdateAvater`, `prevens writin`,
  `projects/exogig`.
  **Profile: good precision, poor recall.** A clean report from it is not evidence of clean code,
  which makes it unusable as a merge gate — the failure mode is silent.
- **qwen/qwen3.8-flash** — TIMEOUT on both fixtures even with an explicit stop condition in the
  spec. Now 1 completion in 4 attempts. Its single good report was the best of the whole bakeoff
  (it alone found the shared-DbContext leak), but a reviewer that finishes a quarter of the time
  cannot gate anything.

⚠️ **Correcting this file's own earlier entry.** The first round's fixture had two *obvious*
defects, and both cheap models found them; I concluded they matched Codex. On the subtle defect —
the kind that actually justifies paying for a reviewer — the cheap models found nothing. **Do not
wire either model into the review gate on the strength of the first round.** Keep them as an
extra cheap opinion alongside Codex, never as a replacement for it.

## 2026-09-05 — board-premise-audit (38 read-only repo audits, monorepo)

**gpt-5.5 · medium · Codex CLI — 34/34 first-try.** Task type was a read-only repo audit producing
one `report.md` per work item, gated by a validator that resolves every `path:line` citation against
the repo. No retries, no hallucinated citations across 34 reports. Reports ran real commands
(including executing repo checkers) and scoped their own residual risk unprompted. This is the
routing pick for repo-audit work.

**openrouter/z-ai/glm-5.2 (OpenCode) — 0/2, no artifact.** Both audition tasks reached attempt 2 and
never wrote `report.md` at all; the check failed on `missing expected files`. Contrast with its 18-task
0.83 pass rate on general code-review — the difference here is a strict output contract (six required
sections + citation format). Worth one more audition on a task with a looser contract before drawing
a conclusion; do not scale it on contract-heavy tasks yet.

**openrouter/cohere/north-mini-code:free — 0/2.** Free, untested, auditioned on two low-stakes tickets.
work#925 wrote a **0-byte** report; work#760 wrote a confident `EVIDENCE-COMPLETE` verdict with **zero**
citations. Both are exactly what the validator exists to catch, so the cost was two cheap failures and
the signal is clean: it does not honour a structured output contract. Not promoted.

**Orchestrator lessons, not model lessons:**
1. **The run was killed by the OS for low memory at ~36/38 tasks** with `max_parallel: 6` alongside a
   second Ringer run and a veleta rsync. The run JSON is then a **stale snapshot** (`state: live`) and
   its pass/fail counts are NOT authoritative. Re-validating the artifacts on disk with the same check
   is cheap, model-free and trustworthy — do that rather than trusting a killed run's bookkeeping.
2. **A verdict vocabulary written for one ticket state inverts in another.** `PREMISE-GONE` meant
   "reject this ticket" for a `ready` item but "the shipped fix is confirmed present" for a `verifying`
   one. The workers handled it sensibly; the label misled the reader. Scope the vocabulary to the
   lifecycle state the batch is actually auditing.

## 2026-09-06/07 — artifact-path scout (51 read-only repo scouts, monorepo)

**gpt-5.5 · medium · Codex CLI — 50/51 first-try, 51/51 after one retry.** Read-only scout over the
monorepo: one ticket per worker, output one `report.md` with five keys, gated by a validator that
resolves the reported path in the repo AND matches the worker's stated rationale back against the
exact ticket text. Median 70s/task. Confirms the 2026-09-05 board-premise-audit result on the same
shape (34/34) — this is the routing pick for read-only repo work, now over 85 tasks.

**The one retry is the most useful row here, and it is an argument for a specific check design.**
`scout-work-545` reported a *correct* path backed by an entirely **fabricated quotation**:
"RegistrationService exposes no update path — Submit() always mints a new row, because the
guest-facing flow is submit-once." Fluent, technical, names a real class and method, and absent from
the ticket. The model invented a rationale and presented it as a quote. Attempt 2 returned the same
path with a real sentence. **A path-existence check would have passed this silently** — the path was
right. Lesson for check authors: when a worker's output will be written down as fact, gate the
*reasoning* against its stated source, not only the artifact. Substring-matching the quote back
against the input is cheap and caught what nothing else could.

**Format tolerance is what makes that assertion usable.** The matcher folds whitespace, smart quotes,
markdown emphasis and case before comparing, with a 25-char floor. An earlier strict version would
have failed honest workers for reflowing a quote across lines — and a wall of format failures trains
workers to stop quoting, which defeats the assertion entirely.

**Orchestrator lessons, not model lessons:**
1. **Two of three runs were OS-killed for low memory — at `max_parallel` 5 AND at 3.** Yesterday's
   kill was at 6. Codex workers are heavier on this box than the free-RAM figure suggests: 13.6 GB
   showed "free" while swap sat at 18.8/19.5 GB and 20 GB was compressed. **Inactive memory is not
   headroom when swap is already full** — read `sysctl vm.swapusage`, not just `vm_stat`. 2 worked.
2. **A killed run's JSON undercounts, consistently and in the safe direction.** Run 1 JSON said 13
   pass; 16 valid reports were on disk. Final tally: JSON 48, disk 51. Workers finish and write
   before the bookkeeping credits them, so re-running the check over the artifacts is both cheaper
   and *more* accurate than trusting the snapshot. Re-validating is model-free and takes seconds.
3. **Clear a killed task's directory before resuming it.** A worker killed mid-write leaves a partial
   `report.md` that a retry can inherit — and a truncated report can satisfy a check on a fragment.
4. **A check can enforce that a quote is real without enforcing that it is the *right* quote.** 3 of
   44 passing reports anchored on an aside, a very short phrase, or an open question from the ticket.
   Orchestrator spot-checks caught those; the gate could not. Budget review time for it.

### Same run, the review side — gpt-5.5 medium as the aios lens on PR #429 (6 rounds)

Six rounds on a 260-line Python gate, one finding per round after the first (which
raised three), decaying P1 → P2 → P2 → P1 → clean → clean. **Every finding held when
checked against its cited `file:line`** — no phantom findings across six rounds, which
is the number worth remembering when deciding whether to argue with this lens.

Three rounds landed on the same assertion, each time a strictly narrower bypass of the
previous fix (no-op command → no-op carrying a path argument → chain of no-ops). That
pattern is the signal to stop patching instances and close the class: the fix that
finally held judged the command *by segment* rather than asking whether it was chained.

**One proposed fix was correctly rejected on data.** The reviewer wanted the check
command to name the reported path. Measured against the run's own 44 reports, 9 do not
name their path — and those 9 are the best checks in the batch (`dotnet test <project>
--filter <name>` gates behaviour through a test project). Adopting it would have failed
the strongest work. Take the finding, verify the fix against real data, and do not
assume the proposed remedy is as sound as the diagnosis.

**Report-contract retries are normal and cheap.** Round 3 failed its own `lens_offtopic`
check (a lens line that did not answer the prohibition it claimed to), retried, passed.
The finding itself was unaffected.

## opencode (harness note, any model)
- 2026-07-28 (code-review, pr82-token-saver-review): GLM 5.2 produced a complete, high-quality 218-line report but could NOT write it to an output directory created by the parent Claude Code process — every write returned EPERM. It then spent ~3000s burning retries on ctypes/`openat`/AppleScript/`sandbox-exec` workarounds until it timed out, and the task logged as FAIL despite the deliverable existing in its taskdir. Codex workers in the same run were unaffected. Lesson: point opencode workers' output INSIDE their own taskdir and harvest via `expect_files`; never hand them a shared output dir another process created. This is an orchestrator spec bug, not a model failure — do not read the FAIL as evidence against GLM.

## Process lessons (2026-07-28, PR #82 review)
- **Ideas worth keeping from a rejected PR.** PR #82's pre-call gateway was dropped (needs your own API key, so it converts flat-rate OAuth plans into metered API billing; incompatible with Claude Code; and it saves tokens by stripping the tool list, which is the thing that makes the CLI worth using). One idea inside it is worth remembering if the problem ever comes back: an *explicitly blessed* answer cache — key a reviewed answer to the exact request plus the exact selected source packet, and replay it with zero upstream calls, never auto-accepting a model answer. It only fires on byte-identical repeats, which is why it didn't justify 2,000 lines here.
- **Doc-stated support floors need a CI job or they are fiction.** README promised Python 3.11+ while CI only ever ran 3.12; a 3.12-only f-string reached review with a fully green suite. Either test the floor or move it.
