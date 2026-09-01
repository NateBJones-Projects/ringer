# Routine/Goal Manifest References Acceptance Design Audit

Scope: read-only acceptance-design review for JAC-3487. I inspected `/Users/hermes/ringer` and the live issue endpoint when it was available. I did not edit `/Users/hermes/ringer`, Paperclip, Beads, git state, services, or trackers. This report is the only file written in the task directory.

## Current Evidence

- JAC-3487 was fetched once from `http://127.0.0.1:3100/api/issues/303e72fa-2be7-44b2-a28b-68900708da84`; it was `in_progress`, `completedAt: null`, and its acceptance criteria require `routine_id`, `goal_id`, projector comments to issue/routine/goal comments, `/Users/hermes/ringer/templates/routine-fanout.json`, Ringside rendering, and an end-to-end Routine run.
- Repeat issue fetch failed: `curl -sS -o /tmp/jac3487-repeat.json -w 'http_code=%{http_code} exit=%{exitcode}\n' ...` exited `1`, printed `curl: (7) Failed to connect to 127.0.0.1 port 3100`, and reported `http_code=000 exit=7`. Acceptance must cover service/network failure behavior.
- `python3 /Users/hermes/ringer/ringer.py --help` exited `0` and showed commands `run`, `lint`, `hud`, `db`, `models`, `catalog`, `demo`, `install-agent`, `uninstall-agent`.
- `cd /Users/hermes/ringer && python3 ringer.py lint templates/probe/manifest.json` exited `0` with `lint: clean (1 tasks)`.
- `TaskSpec` in `/Users/hermes/ringer/ringer.py` currently declares `paperclip_issue` and `bead_id`, but not `routine_id` or `goal_id`. A read-only parser probe using a manifest object containing both IDs exited `0` and printed `routine_attr False goal_attr False`; the fields are silently ignored, not accepted as typed fields.
- `StateWriter.snapshot()` emits task fields including `key`, `status`, `verdict`, `engine`, `model`, `spec`, `verified`, `check`, logs, attempts, tokens, etc. It does not emit `paperclip_issue`, `bead_id`, `routine_id`, or `goal_id`, so projector tests must verify manifest fallback or state serialization explicitly.
- `_run_projection_hook()` triggers only when `task.paperclip_issue or task.bead_id`; Routine/Goal-only manifests will not invoke projection in the current code.
- `/Users/hermes/ringer/hooks/paperclip_projector.py` extracts and posts only `paperclip_issue` and `bead_id`. It has no Routine/Goal target resolver, no idempotency key, no active-issue fallback, and returns `0` even when HTTP results contain errors.
- `/Users/hermes/ringer/templates/routine-fanout.json` is absent (`test -f ...; echo exit=$?` printed `routine-fanout exists exit=1`). A demo manifest exists under `swarms/routines-goals-demo/manifest.json`, but that is not the required template path.
- Ringside source `/Users/hermes/ringer/dashboard/ringside.html` renders task key, status, elapsed time, spec, check, engine/model, logs, and verification proof. I found no `routine_id`, `goal_id`, Routine, or Goal rendering branch in the task view.
- Focused `unittest` evidence:
  - `cd /Users/hermes/ringer && /Users/hermes/homebrew/bin/python3.12 -m unittest tests.test_mock_engine.MockEngineEndToEndTests.test_mock_engine_runs_real_ringer_loop_offline` exited `0`; `Ran 1 test ... OK`.
  - `python3 -m pytest ...` and `/Users/hermes/homebrew/bin/python3.12 -m pytest ...` both exited `1` because `pytest` is not installed.
  - `/Users/hermes/homebrew/bin/python3.12 -m unittest tests.test_lint` exited `1`; one pre-existing template lint failure exists for `templates/pocock-dev-lifecycle/manifest.json` missing `expect_files`.
  - `/Users/hermes/homebrew/bin/python3.12 -m unittest tests.test_paperclip_to_ringer_sync tests.test_hud_server tests.test_mock_engine` exited `1`; HUD test failed to bind `127.0.0.1:0` with `PermissionError: [Errno 1] Operation not permitted` in this sandbox.
  - `py_compile` was not safe here because it attempted to write `__pycache__/ringer.cpython-312.pyc...` in the repo and failed with `Errno 1`; avoid cache-writing commands unless `PYTHONPYCACHEPREFIX` points outside the repo.
- Worktree caveat: `/Users/hermes/ringer` is dirty with pre-existing changes and untracked run artifacts. I did not alter or revert them.

## Acceptance Test Matrix

| ID | Requirement | Setup | Command or pseudocode | Expected result | Diagnostic value |
|---|---|---|---|---|---|
| A1 | Positive parsing of `routine_id` and `goal_id` | Add a unit test in a new focused parser test using valid UUIDs. | `Manifest.from_obj({"run_name":"rg-positive","workdir":tmp,"tasks":[{"key":"t","spec":"x"*90,"check":"test -n ok","routine_id":VALID_ROUTINE,"goal_id":VALID_GOAL}]})` | Parsed `TaskSpec.routine_id == VALID_ROUTINE`, `TaskSpec.goal_id == VALID_GOAL`; no lint finding for the new fields. | Prevents the current false pass where unknown JSON keys are silently ignored. |
| A2 | Malformed UUID rejection | Parametrize `routine_id` and `goal_id` as `not-a-uuid`, short hex, empty whitespace, and a UUID-like string with invalid character. | `with pytest/unittest self.assertRaisesRegex(ValueError, "task t: routine_id must be a UUID")` and same for `goal_id`. | Manifest load fails before execution with field-specific error. | Proves references are typed, not just arbitrary strings. |
| A3 | Wrong-type rejection | Use `routine_id: 123`, `goal_id: {"id": VALID}`, `routine_id: ["..."]`. | `Manifest.from_obj(...)` in unit test. | Raises `ValueError` naming the offending field and expected string UUID. | Catches accidental coercion via `str(...)`, which would hide malformed data. |
| A4 | Backward compatibility | Load existing template manifests without Routine/Goal fields. | `python3 ringer.py lint templates/probe/manifest.json`; unit test `Manifest.from_path(path)` for a legacy manifest. | Legacy manifests still parse and lint as before; default `routine_id == ""`, `goal_id == ""`. | Ensures adding strict UUID validation does not make new fields required. |
| A5 | Issue + Routine + Goal coexistence | Manifest task includes `paperclip_issue`, `bead_id`, `routine_id`, and `goal_id`. Mock all output sinks. | Unit test calls `extract_cross_links()` or new `extract_projection_targets()` against manifest and state. | Target list has exactly one issue target, one bead target, one routine target, one goal target; no dropped or merged target types. | Guards against preserving old issue projection while losing Routine/Goal projection. |
| A6 | Post-run projection idempotency | Use a fake Paperclip server or mocked `urllib.request.urlopen`; run projector twice with the same `run_id`, target IDs, and task verdicts. | `python3 hooks/paperclip_projector.py state.json manifest.json` twice, or unit-call projector with injected client. | Second invocation does not create duplicate comments; it updates/upserts by idempotency key or detects an existing projection marker. Exit and log distinguish created vs already-present. | Prevents retry hooks from spamming Routine/Goal issues. |
| A7 | Target resolution when no active issue exists | Mock Paperclip API: routine exists but has no active issue; goal exists but all issues are done/cancelled. | `resolve_routine_target(routine_id)` / `resolve_goal_target(goal_id)`. | Returns a typed non-fatal skip result such as `target_unavailable:no_active_issue`, with target ID and API status in the projection log; run exit remains determined by worker checks, not projection. | Makes stale Routine/Goal state visible without fabricating a comment destination. |
| A8 | Network/auth failure behavior | Mock `urlopen` to raise connection refused, timeout, `HTTPError(401)`, and `HTTPError(403)`. | Unit test projector main or client methods with temp state/manifest. | Projection returns structured per-target errors, redacts auth material, exits non-zero for direct projector CLI or prints a clear non-fatal hook warning if invoked post-run. Ringer run result must remain based on task verdicts. | Separates worker success from projection outage while preserving operator diagnostics. |
| A9 | Ringside rendering | Create a synthetic run state with task `routine_id`, `goal_id`, and `paperclip_issue`; feed Ringside renderer or DOM test. | Browser/JS test or static render harness: load `dashboard/ringside.html`, inject `/api/runs` payload, expand task. | Expanded task view visibly includes Routine and Goal references with labels and stable IDs; issue remains present; missing references do not render empty chrome. | Proves operator can see links in task view, not just in manifest JSON. |
| A10 | Template availability | Add top-level template file. | `test -f /Users/hermes/ringer/templates/routine-fanout.json && python3 ringer.py lint /Users/hermes/ringer/templates/routine-fanout.json` | File exists, lints clean, has three tasks or clear placeholders for a three-task Routine fan-out, and includes Routine/Goal reference fields. | Directly covers the named acceptance criterion path. |
| A11 | State serialization or manifest fallback | Use a task with only Routine/Goal links; run offline mock engine or instantiate `StateWriter`. | Assert either state JSON contains the links or projector receives the manifest and extracts links from it. | Routine/Goal-only run invokes projection; projector sees target IDs. | Prevents current failure where `_run_projection_hook()` does not run for Routine/Goal-only manifests. |

## Negative Cases

1. Malformed `routine_id` must fail manifest load with a field-specific message. A generic `ValueError` or silent omission is not acceptable.
2. Malformed `goal_id` must fail independently from `routine_id`; tests should cover one valid and one invalid field in both directions.
3. Non-string IDs must not be coerced with `str()`. `123` must not become `"123"` and then fail later in a less diagnostic target resolver.
4. Wrong target type must be rejected or skipped diagnostically: a Routine UUID supplied in `goal_id` should produce `wrong_target_type` from resolver, not post to an arbitrary issue.
5. Network failures must not look like success. The current projector prints `Paperclip X: error: ...` but still returns `0`; acceptance should require per-target status plus a direct CLI non-zero option, while preserving Ringer's fail-open post-run semantics if that is intentional.
6. Auth failures must not echo tokens, headers, or full environment values. Tests should assert redaction in stderr/log output.
7. No-active-issue resolution must not fall back to the newest unrelated issue. It should either post to a Routine/Goal native comment endpoint, if one exists, or skip with a typed reason.
8. Duplicate targets must dedupe by `(target_type, target_id, run_id)`, not by body text alone, because timestamped comments make body-based dedupe unreliable.

## End-to-End Gate

The final executable gate should be one real three-task Routine/Goal run using the deterministic mock engine first, and only then an optional live Paperclip smoke if service ownership allows it.

Setup:
- Create a temp HOME, temp RINGER_HOME, temp XDG_CONFIG_HOME, temp state dir, and temp workdir outside `/Users/hermes/ringer`.
- Use a config with `[engines.mock]` pointing to `/Users/hermes/ringer/engines/mock_worker.py`, artifact disabled, and JSONL eval under temp.
- Use a manifest with exactly three tasks: one issue+routine+goal task, one routine-only task, and one goal-only task. Each writes a distinct file and has a real shell check.
- Use a fake Paperclip HTTP server or injected client that records Routine/Goal/issue comment requests and simulates active issue resolution.

Command:

```bash
HOME="$tmp/home" RINGER_HOME="$tmp/ringer-home" XDG_CONFIG_HOME="$tmp/xdg" \
  /Users/hermes/homebrew/bin/python3.12 /Users/hermes/ringer/ringer.py run "$tmp/routine-goal-e2e.json" \
  --config "$tmp/config.toml" --no-dashboard --identity routine-goal-acceptance
```

Expected result:
- Ringer exits `0`.
- Summary shows all three tasks `pass PASS 1`.
- Each expected file exists with exact content.
- Projection layer records exactly one issue comment, one Routine comment/resolved active Routine issue comment, and one Goal comment/resolved active Goal issue comment per intended target.
- Running the projector a second time against the same state and manifest creates no duplicate comments and reports `already_projected` or equivalent.
- Ringside state/API payload includes enough Routine/Goal data for task rendering, and the rendered task expansion shows both labels and IDs.

This gate is not satisfied by the existing `tests.test_mock_engine...` alone. That test proves the offline Ringer loop works, but it does not include Routine/Goal fields, projection, target resolution, or Ringside reference rendering.

## False-Pass Risks

- Unknown-field false pass: a manifest containing `routine_id` can parse today because the parser ignores unknown keys. The positive test must assert attributes and persisted values, not just "no exception".
- Projection false pass: checking projector exit `0` is insufficient because the current projector returns `0` for HTTP errors. Assert recorded target statuses and comment side effects in a fake server.
- State false pass: if tests pass the manifest directly to the projector but real post-run execution omits `manifest_path`, Routine/Goal targets may be invisible. Include one test through `_run_projection_hook()` or a full run.
- Idempotency false pass: two comments with different timestamps may both be treated as success. Test comment count and idempotency marker, not just response code.
- Resolver false pass: using the JAC-3487 issue ID as a Routine/Goal UUID would test UUID shape, not target semantics. Fake server fixtures must distinguish issue, routine, and goal types.
- Ringside false pass: searching the HTML source for `routine_id` is weaker than rendering an expanded task. Use a DOM assertion against visible text in the expanded worker panel.
- Backward-compatibility false pass: linting only one legacy template misses parser changes affecting worktree manifests. Include one normal legacy manifest and one worktrees template, but keep this small.
- Environment false pass: local Paperclip may be down, as observed. Unit tests must use mocked/fake HTTP; live smoke should be last and explicitly conditional.
- Cache/write false pass: `py_compile` and some test discovery can write `__pycache__`. Set `PYTHONPYCACHEPREFIX="$tmp/pycache"` if compile checks are needed.

## Recommended Execution Order

1. Parser contract tests: A1 through A4. These are fastest and currently would expose the core implementation gap.
2. Projection extraction and target resolver unit tests: A5, A7, A8. Use mocked HTTP; do not require local Paperclip.
3. Projection idempotency test: A6 with a fake comment store.
4. State/hook integration test: A11 to prove Routine/Goal-only manifests actually trigger post-run projection.
5. Template test: A10 for exact required file path and lint cleanliness.
6. Ringside DOM/rendering test: A9 with synthetic state.
7. End-to-end gate: the three-task mock-engine run with fake Paperclip, then optional live Paperclip smoke only when service ownership and credentials are intentionally available.

Recommendation: NO_GO for declaring JAC-3487 complete today. The current checkout does not retain `routine_id`/`goal_id` in `TaskSpec`, does not trigger projection for Routine/Goal-only manifests, lacks Routine/Goal projector resolution/idempotency, lacks the required `/Users/hermes/ringer/templates/routine-fanout.json`, and does not render Routine/Goal references in Ringside.

READ_ONLY
NO_GO
