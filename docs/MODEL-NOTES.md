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
- 2026-07-10 — meridian corpus-ingest dissolution (3 lanes: bash checker fix,
  wrapper authoring, python work-unit + tests): 3/3 pass, one lane needed
  attempt 2 after the executed smoke check caught close-status poisoning the
  worker believed was done (rc=0). Executed checks earn their keep on
  infra-contract work.
- 2026-07-10 — sleep-dissolution-slice-1b (2 lanes: mcp-server staleness
  predicate + tests; extractor/wrapper sweep): 2/2 first-try, additive-schema
  contracts honored, behavioral idempotence check (backdated fixtures) passed.
  Two integration lessons, neither a worker fault: (1) worker-side full-suite
  run hit a sandbox PermissionError because meridian's smoke_test.py writes to
  the REAL repo (test-hermeticity nit in the target repo); (2) the dispatch
  checks ran the mcp-server suite only, while meridian's CD-075 checkpoint
  gate also runs tests/harness-eval (2,817 tests) — the gate caught one
  contract-pinning test the checks couldn't see. Derive check suite scope
  from the target repo's own gate config, not from precedent check scripts.

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
- 2026-07-08 — capability-registry audit (Meridian, 6-YAML semantic docs
  review, code-review type, exploration slot): PASS attempt 1, 112k tokens,
  228s — the only worker of five to nail the output contract first try (all
  4 GLM lanes lost attempt 1 to a report-location miss). Report quality
  high: orchestrator spot-checked 2 findings, both verbatim-confirmed (a
  structured-field schema gap and a CD-conflicting audit-trail location).
  Now 2/2 first-try on review work; one more typed outing reaches proven
  tier for code-review.

- 2026-07-09 — capability-registry-fixes t1 (Meridian, code-feature AUDITION one
  rung up from review): substance passed — created 7 Pydantic classes modeled on
  the REAL storage shapes (orchestrator verified id/enqueued_at/owner_session
  against storage.py; my spec had guessed wrong names and kimi followed code over
  spec, as instructed), and honored "change server.py minimally or not at all"
  with zero wire-format edits. Recorded FAIL x2 was orchestrator-side: relative
  fix-summary path (spec-craft) + retry killed by the OpenRouter key-limit 403.
  64k tokens. Treat as effective first-try substance; keep the code-feature
  audition open.

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


## opencode / GLM-5.2 (2026-07-09, capability-registry-fixes — YAML surgery + test-hardening)
- Round 1: t2/t3 official PASS attempt 1 (subtle close-ordering contradiction fix
  quoted both sides; 7-file enum+notes sweep clean). t4/t5 substance verified
  green orchestrator-side (when_to_use x5 grounded in real responsibilities;
  unsatisfiable-invariant removal preserved reasoning as a comment) — their FAILs
  were the missing relative-path fix-summary + 403-dead retries, ruled FOR the
  worker. Round 2 (absolute paths): t6 docs + t7 test-hardening PASS attempt 1
  (92s/85s). One caution: t7's comment cited an INVENTED CD number as the
  convention authority — caught at CSA review; specs should hand workers the
  exact citation to use, and review should assume plausible-looking references
  are unverified.

## north-mini-code (via opencode, `openrouter/cohere/north-mini-code:free`)
- 2026-07-09 — AUDITION FAILED (t6 exploration slot, $0): a 3-file mechanical
  comment repoint, and it never oriented to its worktree — read and attempted
  edits via the MAIN repo's absolute path (Seatbelt correctly blocked every
  write), wrote an honest blocker summary, then its task dir vanished mid-verify
  (crashed the run — see process lesson). Do not re-audition on repo-editing
  tasks; if it gets another slot, use a pure-artifact task (writes only to its
  own dir).

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

- 2026-07-09 — the absolute-path lesson bit a SECOND time: the fix-swarm
  template's preamble says './fix-summary.md' (relative), and three lanes of
  real YAML work failed [missing_summary] over it. The 2026-07-08 rule
  ("spec deliverable paths as ABSOLUTE") applies to the template's own
  boilerplate, not just report deliverables — fix the template.
- 2026-07-09 — OpenRouter per-KEY total spend limit returned 403 mid-run:
  every retry plus one whole lane died at 0 tokens ("Key limit exceeded
  (total limit)"). Distinct from account credit; nothing in preflight
  (`models`, `catalog`) surfaces it. Worth a pre-run credit/limit probe.
- 2026-07-09 — ringer robustness: when a task dir vanished before verify
  (north-mini audition), `run` crashed with argparse-style exit 2 (ENOENT)
  instead of recording FAIL and continuing; one crashed lane took the whole
  run's summary path down with it.

## codex (2026-07-06, bench-operator-proofing)
- 8/8 code-feature tasks passed attempt 1 across 3 rounds (worktrees mode, Python harness refactor; 108k-406k tokens/task). Specs embedded the approved architecture doc + exact file ownership; checks built fresh uv venvs and ran the full pytest suite.
- Lesson (check design, not model): all 3 post-integration bugs were invisible to the checks — a test that passed only because the worker's worktree lacked .env, a `--help`-only assertion missing a runtime importlib/sys.modules bug (py3.12 dataclasses), and bare console-script names failing outside activated venvs. Checks should exercise one real invocation from a cold shell, not just --help.

## opencode / GLM-5.2 (2026-07-08, mcp-local-rag-review)
- 3/4 code-review tasks passed (server attempt 1; vectordb + parser-pdf on attempt 2). Report quality high — orchestrator spot-checked 4 findings against source, 4/4 confirmed verbatim (incl. two P1s). 32k-112k tokens/task.
- chunker-embedder failed both attempts, but the autopsy ruled FOR the worker: attempt 1 wrote no report.md (retry fixed that); attempt 2's report was excellent and died on the 1200-word cap at 1359 words. Check-craft lesson: hard length caps fail honest work — use ~1600 or have the spec say "trim evidence quotes if over," and treat length as format, not substance.
- Same run, codex engine: 0/4 in 0.2s each — macOS XProtect had deleted the codex binary (spawn ENOENT). Environment failure, not model signal: disregard those 8 codex failure rows when reading the scoreboard.
- **RESOLVED 2026-07-09** — root cause: Apple **revoked OpenAI's Developer ID cert** (fallout from the 2026-03-31 axios CI supply-chain compromise), so macOS deletes any binary still signed with it. That is why only the OpenAI-signed `codex` binary was removed while its sibling ripgrep survived — a cert-revocation match, not a content/YARA match. The installed 0.130.0 still carried the revoked-lineage cert; the public "Codex CLI 0.119.0+ is safe" guidance did NOT hold for the npm binary. Fix: `npm install -g @openai/codex@latest` → **0.144.0**, re-signed with a valid Developer ID (`OpenAI OpCo, LLC / 2DC432GLL2`), verified end-to-end — `codesign --verify` passes, `codex exec` (read-only sandbox) reached gpt-5.5 and returned the expected token, binary persists on disk. 0.144.0 also relocated the binary to `vendor/aarch64-apple-darwin/bin/codex`. Recurrence guard: a codex lane going 0/N at ~0.2s with spawn ENOENT is another cert/XProtect removal, not a model signal — reinstall the latest re-signed build, don't debug the model.

## opencode / GLM-5.2 (2026-07-08, mcp-local-rag-fixes — worktrees code-fix)
- Effectively 4/4 on real TypeScript fixes with regression tests (3 official PASS; the embedder-dispose-race FAIL was a harness artifact — see below — and the worker's epoch-fence fix + red→green test + full 870-test suite all passed orchestrator-side). 62k-95k tokens/task. The dispose-race fix (epoch fence, await-in-flight, test seam extraction) is genuinely sophisticated work from a cheap model.
- Harness lesson (worktrees + node ecosystems): instructing workers to `ln -sfn <main>/node_modules node_modules` collides with a `node_modules/` (trailing-slash) .gitignore — the trailing slash matches DIRECTORIES only, so the SYMLINK is not ignored, the check's own `git add -A` patch-export stages it, and every later attempt false-fails `outside_owned_files`. Workers cannot repair it: a worktree's .git lives in the main repo, outside the task sandbox (by design). Fix: add `node_modules` (no slash) to the target repo's `.git/info/exclude` before worktree runs (local-only, done for mcp-local-rag), and remember retries inherit attempt 1's dirty index.
- "database is locked" at spawn: shared opencode.db startup-write collision when several workers launch in the same instant; losers die instantly and burn their retry. Mitigated 2026-07-08 with random pre-spawn jitter in engines/opencode-sandboxed.sh; made a *uniform* 0-4s on 2026-07-09 (the fraction is now `printf '%02d'`-padded — a bare `$((RANDOM % 100))` produced "3.5" = 3.5s, not "3.05", underweighting the low end). This is a wrapper-level band-aid, not a cure: Ringer doesn't own opencode's DB connection, so it can't set the `busy_timeout=5000` + WAL it already uses on its own DB (ringer.py:4916) — the real fix (busy_timeout on opencode.db) belongs upstream *in opencode itself*. The jitter commit stays a clean cherry-pick if we ever offer it to upstream Ringer, but that PR was not opened.

## opencode / GLM-5.2 (2026-07-08, capability-registry-audit — semantic docs review)
- 4/4 PASS on attempt 2 (23k-54k tokens, 41-202s on the passing attempts). All four attempt-1 FAILs were the identical miss: report.md absent from the task dir at check time — workers exited 0 after 60-100k tokens of real reading, so the report went somewhere else or nowhere; the injected "missing expected files" retry context fixed all four instantly. Report substance high: orchestrator spot-checked 3 findings, 3/3 verbatim-confirmed, including a real cross-sibling factual contradiction and an unsatisfiable-invariant catch. Not a model-quality signal — a spec-craft miss (see process lesson below); kimi avoided the same trap on the same spec wording.
- 2026-07-08 process lesson (spec-craft): "write report.md in your current working directory" is ambiguous for workers that cd into the repo they are auditing — 4 of 5 lanes lost attempt 1 to it. Spec deliverable paths as ABSOLUTE (the task dir is known at manifest-write time: <workdir>/<key>/report.md). Retry absorbed everything, but it doubled wall-clock and depressed first-try rates over a miss that never tested substance.

## codex (2026-07-10, run corpus-ingest-dissolution, meridian repo-feature/worktrees)
- 3 tasks (code-fix bash surgical / code-feature 614-line bash wrapper / code-feature python work-unit+tests): 3/3 pass, one retry on the python lane. Tokens: 37k / 148k / 275k.
- Retry cause was SPEC-side: my spec mandated strict failure semantics (missing dependency → work-unit failed) that an existing end-to-end smoke test correctly rejected (close status poisoned). Codex attempt 2 satisfied both by forking semantics on a test-plumbing field (changeset_overrides is None) — working but over-clever; simplified at integration. Lesson: when a spec adds a unit to an orchestrator that has end-to-end smoke tests, spec the desired status mapping for missing-dependency explicitly.
- Both fixture-based checks passed while hiding 2 real-substrate bugs (EXIT-trap lock cleanup deleting a live lock; tilde-expansion in ${var#~/} — fixture used absolute paths). Codex not at fault; checks were fixture-only. Lesson: for ops scripts, add one real-repo scenario to the check or budget an acceptance pass after integration.

## gpt-5.6-sol (codex)
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

## run verification-ladder-pilot 2026-07-11 (code-review: adversarial review of a design proposal vs repo snapshot, structural+citation executed checks)
- **codex (reasoning=high):** pass on attempt 2, 155k tok, 264s — attempt-1 fail was ONE invalid ID token (`CD-079-era`) caught by the citation check; retry removed it cleanly. Deepest unique findings of the panel (dispatcher-governance conflict nobody else saw); all four contested stat sub-claims verified exact on orchestrator recheck. Evidence-refutation vs a file tree is squarely its lane.
- **kimi-k2.7-code:** pass attempt 1, 168k tok. Consequence lens: named the fix that had actually shipped nearly verbatim, and beat the benchmark's own 6-agent pipeline on a fact it got wrong (existing-tests). 2/2 first-try on code-review now — promotion-track for review lanes.
- **glm-5.2:** pass attempt 1 (~88k tok) despite 0.12 first-try history — long-document review with a strong structural contract suits it better than long-spec apply-rubric work (cf. 7/8 stall). Held the frame-challenge lens credibly: quoted the decisive YAML line, computed exact latency stats from a 576-record JSONL unprompted.
- **nemotron-3-super-120b:free:** FAIL x2 (235k tok, $0) — wrote review prose with ## Summary but never produced `Finding:` blocks even with check output injected into retry. Second contract-compliance failure class after 7/8 (then: no file; now: file, wrong structure). Demotion confirmed — do not re-audition on structured-contract review work.

## run verification-ladder-pilot Phase 2, 2026-07-11 (code-review over 3 lifecycle/CD artifacts vs repo snapshot; 12 lanes + 3-lane retry)
- **Infra lesson:** OpenRouter credits ran dry mid-run — 402s killed 3 lanes and masqueraded as model failures until raw-log reads. Check the balance before multi-lane OpenRouter runs; a 402 mid-batch contaminates exploration-seat data.
- **Check lesson (landed in the pilot pack's checker copy):** exact-label matching burned honest work three times across two models (`**Finding:**` bold + `## Finding` headings). Normalize format variants before matching; keep substance strict. Also fixed: citation checker truncated space-containing filenames → false MISSING (prefix-match fallback).
- **codex (reasoning=high):** 3/3 (one attempt-2 was its own doing in Phase 1; here 2×attempt-1, 1×attempt-2). Deepest verified material again — silent code branches, whole-file-grep resolution defect, discharge-evidence audit. The evidence lane benchmark.
- **kimi-k2.7-code:** 1/3 clean (flawless labels on the prose CD unit) but bold/heading label drift on both YAML-artifact units even on retry (2×2). Content when read was top-tier (a P0 sequencing finding nobody else had). Long/structured-input contract decay is the pattern to watch; budget retries or relax checks in code.
- **glm-5.2:** 2/3 + 1 false-fail rescued (check bug, not model). Strong runtime-evidence usage; emitted one stray CJK token mid-prose (cosmetic). Holding frame-challenge credibly two phases running — promotion-track for review lanes at its price.
- **deepseek-v3.2 (exploration seat):** 1/6 clean first-try, 2 harvested post-relaxation, and the two lanes that read lifecycle records got the semantics wrong (treated `discharged` as pending; refuted pre-fix history with post-fix code). Passable grounding lists, weak review judgment on stateful/temporal artifacts. Probation stands; don't seat it on lifecycle-record review.

## run verification-ladder-pilot amendment-selftest, 2026-07-11 (code-review: the ladder amendment draft reviewed by the band it defines)
- **codex (reasoning=high):** pass attempt 1, 177s. Five findings, 5/5 confirmed on orchestrator disk-verification — including the check-semantics catch (the "live-repo tripwire" is a citation check over report text, not access control) that reshaped the amendment's contamination-control claim. Evidence lane benchmark holds across the whole arc.
- **kimi-k2.7-code:** pass attempt 1, 309s, flawless label contract on prose-CD input (consistent with Phase 2: contract decay only on YAML-artifact units). Eight findings: six confirmed incl. the run's only P0 that survived (egress-acceptance gap, cited capability YAML + CD-20260612 verbatim); one REFUTED at synthesis (claimed a boundary "already breached" — the record shows operator confirmed each disposition). Severity-inflation now a 2-run pattern: read kimi P0s skeptically, verify twice.
- **glm-5.2:** pass attempt 1, 283s. Three frame findings, all at least partially confirmed — the Phase1-vs-Phase2 non-comparability catch (bands-compose is a hypothesis, not established) was the most decision-relevant finding of the run. Three phases running holding frame-challenge; promotion earned for review lanes at its price.
- **deepseek-v3.2 (exploration seat):** FAIL ×2, 265s — coherent 7-finding report under `## Finding N:` headings instead of the required `Finding:` label lines, both attempts, with check output injected on retry. Third contract-failure class across the arc. Demoted from this panel; do not re-seat on structured-contract review. Two micro-findings were adopted after orchestrator verification (auxiliary; scoreboard row stays FAIL).

## cohere/north-mini-code:free
- 2026-07-15 (code-feature, retrieval-transport-build/patch3): FAILED 2/2 attempts on a 3-file config+docs authoring task with a strong executable check. Wrote empty files plus a stray test.txt in its worktree; attempted `cd` to the real repo path (OpenCode sandbox absorbed it — no contamination). Cannot follow worktree-boundary + multi-file authoring specs. Demoted; do not re-audition on code-feature. Task re-routed to codex same day.

## run hud-ghost-run-fix, 2026-07-15 (code-fix: dead-pid reconcile in Ringer's own HUD runs feed)
- **codex (reasoning=medium):** pass attempt 1, 126.5s, 37.5k tokens. Implemented a tightly-specified 16-line helper plus 5 unit tests exactly to brief, with zero scope drift into neighbouring code. All three orchestrator mutation tests (ignore liveness / drop the wiring / persist to disk) were caught by the intended test, so the coverage bites rather than decorates — worth spot-checking this way whenever a worker writes its own tests, since a green suite alone can't distinguish the two. Raised a real objection in notes.md instead of silently deviating: the brief's int-only pid guard is stricter than the `_prune_active_runs()` precedent it cited (which coerces via `int()`). The objection was correct and the strict rule was kept deliberately — a string pid means a corrupted state file, and refusing to declare death on it is the safe direction. Also correctly reported 7 sandbox socket-binding errors as an environment limit rather than claiming a clean suite; the check (run unsandboxed by the orchestrator) and an independent re-run both showed 186/186. Medium effort is the right lane for a surgical, fully-specified patch — don't pay for high here.
