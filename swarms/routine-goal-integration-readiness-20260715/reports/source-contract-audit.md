# Source Contract Audit: JAC-3487 Ringer Routine/Goal References

## Scope

Read-only audit requested for the live `/Users/hermes/ringer` checkout and Paperclip issue JAC-3487:

- Issue endpoint inspected: `http://127.0.0.1:3100/api/issues/303e72fa-2be7-44b2-a28b-68900708da84`
- Checkout inspected: `/Users/hermes/ringer`
- Additional live hook inspected because `ringer.py` invokes it at runtime: `/Users/hermes/.ringer/hooks/paperclip_projector.py`
- No modifications were made to `/Users/hermes/ringer`, Paperclip, Beads, git state, services, or trackers.
- This report is the only intentional write in this task directory.

JAC-3487 acceptance criteria from the first successful issue fetch:

- Ringer manifests accept `routine_id` and `goal_id` fields without validation errors.
- Post-run hook projects verdicts to both Paperclip issue comments and Routine/Goal comments.
- Manifest template exists at `/Users/hermes/ringer/templates/routine-fanout.json`.
- Ringside HUD displays Routine/Goal references in task view.
- End-to-end Ringer run with Routine reference completes successfully.

## Evidence

### Issue and workspace state

- Command run:
  - `curl -fsS http://127.0.0.1:3100/api/issues/303e72fa-2be7-44b2-a28b-68900708da84`
  - Observed: returned JAC-3487 JSON with `status:"in_progress"`, `issueNumber:3487`, title `Implement Ringer manifest references to Paperclip Routines/Goals`, and the acceptance criteria listed above.
- Later retry command:
  - `curl -sS -o /tmp/paperclip-jac3487-refetch.out -w 'http_code=%{http_code} exit=%{exitcode}\n' http://127.0.0.1:3100/api/issues/303e72fa-2be7-44b2-a28b-68900708da84`
  - Observed: `curl: (7) Failed to connect to 127.0.0.1 port 3100`; `http_code=000 exit=7`.
- Listener check:
  - `lsof -nP -iTCP:3100 -sTCP:LISTEN`
  - Observed: `node ... TCP 127.0.0.1:3100 (LISTEN)`.
- Process detail check:
  - `ps -axo pid,command | rg -i 'paperclip|3100'`
  - Observed: `zsh:1: operation not permitted: ps`.

### Git and dirty-tree caveat

- Command run:
  - `git -C /Users/hermes/ringer status --short --branch`
- Observed:
  - Branch: `fleet-sync/paperclip-to-ringer-20260709...fleet/fleet-sync/paperclip-to-ringer-20260709`
  - Modified tracked files: `README.md`, `docs/MODEL-NOTES.md`, `registry/model-identity.toml`
  - Many untracked paths, including `.ringer/`, `artifacts/`, `jobs/`, several `manifests/*.json`, `swarms/routine-goal-integration-readiness-20260715/`, `swarms/routines-goals-demo/`, and `templates/pocock-dev-lifecycle/`.
  - `git status --porcelain=v1 | wc -l` reported `28`.
- Command run:
  - `git -C /Users/hermes/ringer rev-parse --short HEAD`
- Observed:
  - `3e38e51`.

### Source search

- Command run:
  - `rg -n "routine_id|goal_id|Routine|Goal|routine-fanout|TaskSpec|Manifest|manifest" /Users/hermes/ringer/ringer.py /Users/hermes/ringer/hooks /Users/hermes/ringer/dashboard /Users/hermes/ringer/hud /Users/hermes/ringer/templates /Users/hermes/ringer/tests /Users/hermes/ringer/schema /Users/hermes/ringer/swarms/routines-goals-demo`
- Relevant observed hits:
  - `/Users/hermes/ringer/ringer.py:385` defines `class TaskSpec`.
  - `/Users/hermes/ringer/hooks/paperclip_projector.py` references only `paperclip_issue` and `bead_id` in source.
  - `/Users/hermes/ringer/swarms/routines-goals-demo/manifest.json:14-15` contains `routine_id` and `goal_id`.
  - `/Users/hermes/ringer/swarms/routines-goals-demo/manifest.json:24` contains `goal_id`.
  - No `routine_id` or `goal_id` implementation hits appeared in `/Users/hermes/ringer/ringer.py`, `/Users/hermes/ringer/dashboard/ringside.html`, `/Users/hermes/ringer/hud/frontend/hud.js`, or `/Users/hermes/ringer/hud/src/main.rs`.
- Command run:
  - `rg -n "routine_id|goal_id" /Users/hermes/ringer --glob '!**/.git/**'`
- Relevant observed hits:
  - `/Users/hermes/ringer/README.md:109-110` documents the fields.
  - `/Users/hermes/ringer/swarms/routines-goals-demo/manifest.json:14-15,24` contains them.
  - `/Users/hermes/ringer/swarms/routine-goal-integration-readiness-20260715/swarm.json` contains this audit prompt.
  - No source implementation hit in `ringer.py` or checkout `hooks/paperclip_projector.py`.

### TaskSpec and manifest parser

- Source lines:
  - `/Users/hermes/ringer/ringer.py:385-403`: `TaskSpec` fields include `key`, `spec`, `check`, `engine`, `expect_files`, `timeout_s`, `full_access`, `engine_args`, `verified`, `model`, `task_type`, `paperclip_issue`, and `bead_id`. There are no `routine_id` or `goal_id` fields.
  - `/Users/hermes/ringer/ringer.py:444-449`: `from_obj()` validates `paperclip_issue` and `bead_id` as strings. There is no equivalent validation for `routine_id` or `goal_id`.
  - `/Users/hermes/ringer/ringer.py:450-464`: `TaskSpec` construction passes `paperclip_issue` and `bead_id`, but not `routine_id` or `goal_id`.
  - `/Users/hermes/ringer/ringer.py:494-512`: `Manifest.from_obj()` parses tasks through `TaskSpec.from_obj()`.
- Command run:
  - `PYTHONDONTWRITEBYTECODE=1 /Users/hermes/homebrew/bin/python3.12 - <<'PY' ... Manifest.from_path('swarms/routines-goals-demo/manifest.json') ...`
- Observed output:
  - `tasks 2`
  - `routine-linked-task has routine_id attr: False has goal_id attr: False`
  - `goal-only-task has routine_id attr: False has goal_id attr: False`
  - `paperclip_issue JAC-3442 bead_id (empty)` for the first task.
  - `paperclip_issue (empty) bead_id (empty)` for the second task.
- Malformed-type probe command:
  - `PYTHONDONTWRITEBYTECODE=1 /Users/hermes/homebrew/bin/python3.12 - <<'PY' ... routine_id:123, goal_id:{'bad':'type'} ...`
- Observed output:
  - `parsed_task_keys ['bead_id', 'check', 'engine', 'engine_args', 'expect_files', 'full_access', 'key', 'model', 'paperclip_issue', 'spec', 'task_type', 'timeout_s', 'verified']`
  - `routine_attr False goal_attr False`
  - `lint_findings [...]` with findings unrelated to Routine/Goal field type.

### CLI lint behavior

- Command run:
  - `PYTHONDONTWRITEBYTECODE=1 ./ringer.py lint swarms/routines-goals-demo/manifest.json`
- Observed with default `/usr/bin/python3`:
  - `ringer requires Python 3.11+ (tomllib); found 3.9.6 at /Library/Developer/CommandLineTools/usr/bin/python3`
- Interpreter discovery:
  - `python3.12 -> /Users/hermes/homebrew/bin/python3.12`, `Python 3.12.13`
  - `python3.11 -> /Users/hermes/.hermes/hermes-agent/venv/bin/python3.11`, `Python 3.11.15`
  - `/Users/hermes/homebrew/bin/python3 -> /Users/hermes/homebrew/bin/python3`, `Python 3.14.6`
- Command run:
  - `PYTHONDONTWRITEBYTECODE=1 /Users/hermes/homebrew/bin/python3.12 ringer.py lint swarms/routines-goals-demo/manifest.json`
- Observed output and exit:
  - Exit code `1`.
  - `lint: goal-only-task: spec is probably underspecified; workers are stateless and cannot ask questions.`
  - No `routine_id` or `goal_id` validation error appeared.

### Manifest schema files

- Command run:
  - `find /Users/hermes/ringer/schema -maxdepth 2 -type f -print -exec sh -c 'echo --- "$1"; sed -n "1,160p" "$1"' sh {} \;`
- Observed:
  - Only `/Users/hermes/ringer/schema/launch-receipt.v1.json`.
  - That schema covers launch receipts, not Ringer manifest task fields.
  - `/Users/hermes/ringer/schema/launch-receipt.v1.json:74` includes `bead_id`; it does not define `routine_id` or `goal_id`.

### Projector source and installed hook drift

- Checkout source lines:
  - `/Users/hermes/ringer/hooks/paperclip_projector.py:32-49`: posts comments to `/api/issues/{issue_id}/comments`.
  - `/Users/hermes/ringer/hooks/paperclip_projector.py:158-182`: `extract_cross_links()` extracts only `(paperclip_issue, bead_id)`.
  - `/Users/hermes/ringer/hooks/paperclip_projector.py:215-227`: main loop posts Paperclip issue and Beads comments only.
  - `/Users/hermes/ringer/hooks/paperclip_projector.py:229-242`: writes projection log under `~/.ringer/hooks`; I did not execute this hook because that would mutate state.
- Runner source lines:
  - `/Users/hermes/ringer/ringer.py:7099-7107`: `_run_projection_hook()` gates hook execution on `task.paperclip_issue or task.bead_id`.
  - `/Users/hermes/ringer/ringer.py:7107`: hook path is `Path.home() / ".ringer" / "hooks" / "paperclip_projector.py"`.
  - `/Users/hermes/ringer/ringer.py:7113-7115`: when invoked, the runner passes the run-state path and manifest path.
- Installed hook check:
  - Command: `if test -f /Users/hermes/.ringer/hooks/paperclip_projector.py; then ... shasum -a 256 ...; rg -n "routine_id|goal_id|paperclip_issue|bead_id" ...; fi`
  - Observed: `HOOK_PRESENT`.
  - Hashes differ:
    - Installed hook: `557dbbdc7bba78e93616f6cae6914b10d96f9d9f55be144c5f3fdae7bfaae027`
    - Checkout source hook: `2dd421e8e08feaa4ea39cb35917b5d2bfd8cf4447ba8775b6e51fe4528980d8a`
- Installed hook lines:
  - `/Users/hermes/.ringer/hooks/paperclip_projector.py:10-13`: docstring claims `routine_id` and `goal_id` projection.
  - `/Users/hermes/.ringer/hooks/paperclip_projector.py:168-216`: extracts `paperclip_issue`, `bead_id`, `routine_id`, and `goal_id`.
  - `/Users/hermes/.ringer/hooks/paperclip_projector.py:253-266`: `find_routine_active_issue()`.
  - `/Users/hermes/.ringer/hooks/paperclip_projector.py:269-283`: `find_goal_linked_issues()`.
  - `/Users/hermes/.ringer/hooks/paperclip_projector.py:290-319`: post Routine/Goal progress.
  - `/Users/hermes/.ringer/hooks/paperclip_projector.py:407-440`: main loop posts issue, Beads, Routine, and Goal comments.
- Non-mutating extractor probe:
  - Command imported both hook modules and called `extract_cross_links()` without running `main()`.
  - Observed output:
    - `repo [('JAC-3487', '')]`
    - `installed [{'paperclip_issue': 'JAC-3487', 'bead_id': '', 'routine_id': 'routine-1', 'goal_id': 'goal-1'}]`
- Projection gate probe:
  - Command constructed two manifests in memory and evaluated the same gate as `/Users/hermes/ringer/ringer.py:7101-7103`.
  - Observed output:
    - `routine_only projection_hook_gate False paperclip_issue (empty) bead_id (empty)`
    - `issue_plus projection_hook_gate True paperclip_issue JAC-3487 bead_id (empty)`

### Template evidence

- Command run:
  - `if test -f /Users/hermes/ringer/templates/routine-fanout.json; then echo PRESENT; else echo MISSING; fi`
- Observed:
  - `MISSING`
- Command run:
  - `find /Users/hermes/ringer/templates -maxdepth 2 -type f | sort | sed -n '1,80p'`
- Observed:
  - Many existing kit files, but no `/Users/hermes/ringer/templates/routine-fanout.json`.
- Demo manifest lines:
  - `/Users/hermes/ringer/swarms/routines-goals-demo/manifest.json:1-27`.
  - It is under `swarms/`, not `templates/`.
  - It has 2 tasks at lines `5-26`, not a 3-task Routine fan-out.

### Ringside / HUD display evidence

- State serialization lines:
  - `/Users/hermes/ringer/ringer.py:1206-1235` writes each task state with `key`, `status`, `verdict`, `engine`, `model`, `spec`, `spec_short`, `verified`, `check`, check result fields, `timeout_s`, `taskdir`, `log_path`, report paths, deliverables, activity, elapsed, tokens, attempts, children, and log tails.
  - It does not include `paperclip_issue`, `bead_id`, `routine_id`, or `goal_id`.
- Browser Ringside renderer lines:
  - `/Users/hermes/ringer/dashboard/ringside.html:930-959`: worker row shows glyph, key/name, state, elapsed time, and activity.
  - `/Users/hermes/ringer/dashboard/ringside.html:972-1010`: expanded task view shows brief/spec, pass test/check, engine/model, attempts, and polling/final state.
  - `/Users/hermes/ringer/dashboard/ringside.html:1034-1062`: final proof view shows `verified` and `check_output_tail`.
  - No Routine/Goal rendering is present in those task-view ranges.
- Tauri HUD evidence:
  - `/Users/hermes/ringer/hud/frontend/hud.js:71-97`: HUD consumes run payloads and renders window title counts/status.
  - `/Users/hermes/ringer/hud/src/main.rs:370-393`: task log advertisement checks only `log_path` and `taskdir`.
  - `/Users/hermes/ringer/hud/src/main.rs:554-566`: task elapsed aggregation only.
- Search command:
  - `rg -n "paperclip_issue|bead_id|routine_id|goal_id|Task|task\." /Users/hermes/ringer/dashboard/ringside.html /Users/hermes/ringer/hud/frontend/hud.js /Users/hermes/ringer/hud/src/main.rs`
- Observed:
  - Hits for generic task fields, but no `routine_id`, `goal_id`, `paperclip_issue`, or `bead_id`.

### End-to-end run evidence

- I did not run a real Ringer Routine/Goal end-to-end run because the user explicitly prohibited modifying `/Users/hermes/ringer`, services, trackers, Paperclip, Beads, and git state. A real run would write run state, workdirs, artifacts, and likely hook logs/comments.
- Search command:
  - `rg -n "routines-goals-demo|routine-linked-task|goal-only-task|9ab1a748|219e8069|routine_id|goal_id" /Users/hermes/ringer/.ringer /Users/hermes/ringer/artifacts /Users/hermes/ringer/jobs /Users/hermes/ringer/swarms /Users/hermes/ringer/manifests 2>/dev/null`
- Observed:
  - Only the audit swarm prompt, the `swarms/routines-goals-demo/manifest.json`, and unrelated `jobs/degraded-ledger-resolution` `goal_id` text.
  - No completed `routines-goals-demo` run evidence.
- Search command:
  - `rg -n "routines-goals-demo|9ab1a748|219e8069|routine_id|goal_id|routine-linked-task|goal-only-task" /Users/hermes/.ringer/runs /Users/hermes/.ringer/artifacts /Users/hermes/.ringer/manifests /Users/hermes/.ringer/hooks/projection_log.jsonl 2>/dev/null`
- Observed:
  - `/Users/hermes/.ringer/hooks/projection_log.jsonl:19` contains a synthetic-looking `test-routine` entry with `test-routine-id` and `test-goal-id`.
  - Current audit run states contain the audit prompt and previous audit text.
  - No observed completed real `routines-goals-demo` or JAC-3487 Routine-reference run.

## Acceptance Matrix

| Criterion | Status | Evidence |
|---|---:|---|
| TaskSpec/schema support for `routine_id` and `goal_id` | MISSING | `TaskSpec` lacks both fields at `/Users/hermes/ringer/ringer.py:385-403`; parser probe printed `has routine_id attr: False` and `has goal_id attr: False`; schema directory contains only `launch-receipt.v1.json`. |
| Ringer manifests accept `routine_id` and `goal_id` without validation errors | PARTIAL | A manifest containing the fields parsed far enough for lint, and no Routine/Goal validation error appeared. This is permissive unknown-key acceptance, not first-class support: malformed `routine_id:123` and object `goal_id` were silently dropped. |
| Manifest validation enforces a Routine/Goal contract | MISSING | No field, type, UUID-shape, or target-resolution validation exists in `TaskSpec.from_obj()` or a manifest schema. |
| Post-run hook projects to Paperclip issue comments | PRESENT | Checkout hook source posts issue comments at `/Users/hermes/ringer/hooks/paperclip_projector.py:32-49` and loops on `paperclip_issue` at `215-221`. Not executed because it would mutate Paperclip/log state. |
| Post-run hook projects to Routine/Goal comments | PARTIAL | Checkout hook source is missing Routine/Goal support. Installed hook at `/Users/hermes/.ringer/hooks/paperclip_projector.py:168-216` and `290-319` has Routine/Goal logic, but it is source-drifted from the checkout and was not executed. Runner gate only fires for parsed `paperclip_issue` or `bead_id`, so routine-only/goal-only manifests do not project. |
| Projector handles issue plus Routine/Goal targets together | PARTIAL | Non-mutating extractor probe showed installed hook returns all four fields for an issue+routine+goal manifest, while repo hook returns only issue/bead. Runtime execution remains unverified because posting would mutate Paperclip/logs. |
| Routine-only or Goal-only projection | MISSING | Projection gate probe printed `routine_only projection_hook_gate False`; `/Users/hermes/ringer/ringer.py:7101-7103` does not consider `routine_id` or `goal_id`. |
| `templates/routine-fanout.json` exists | MISSING | `test -f /Users/hermes/ringer/templates/routine-fanout.json` printed `MISSING`. |
| Routine fan-out template contains a sample Routine with 3 tasks | MISSING | Only `/Users/hermes/ringer/swarms/routines-goals-demo/manifest.json` exists; it is untracked, under `swarms/`, and has 2 tasks. |
| Ringside HUD displays Routine/Goal references in task view | MISSING | State payload omits the fields at `/Users/hermes/ringer/ringer.py:1206-1235`; dashboard task view renders spec/check/model/proof only at `/Users/hermes/ringer/dashboard/ringside.html:972-1062`; `rg` found no Routine/Goal render hits. |
| Real end-to-end Ringer run with Routine reference completes successfully | UNVERIFIED | I did not execute a real run due read-only constraints. Searches found no completed real `routines-goals-demo` or JAC-3487 Routine-reference run state. Existing hook log line 19 appears synthetic (`test-routine-id`, `test-goal-id`) and is not enough to pass this criterion. |

## Findings

1. The checkout source does not implement first-class `routine_id` or `goal_id` on `TaskSpec`.
   - Evidence: `/Users/hermes/ringer/ringer.py:385-403` has no fields; `/Users/hermes/ringer/ringer.py:444-464` validates/persists only `paperclip_issue` and `bead_id`.
   - The parser accepts manifests with Routine/Goal keys only because unknown keys are ignored.

2. Manifest validation is not a real contract for Routine/Goal IDs.
   - Evidence: malformed in-memory manifest with numeric `routine_id` and object `goal_id` parsed successfully and dropped both fields.
   - This can create false passes: a lint command can avoid Routine/Goal errors while the IDs are unusable downstream.

3. The source hook and installed hook disagree.
   - Evidence: checkout hash `2dd421e...` differs from installed hash `557dbbd...`.
   - Checkout hook lacks Routine/Goal logic; installed hook contains it.
   - Because the source checkout is the contract under audit, this drift is not acceptable completion evidence.

4. Runtime projection is gated on the old fields.
   - Evidence: `/Users/hermes/ringer/ringer.py:7101-7103` checks only `task.paperclip_issue or task.bead_id`.
   - Probe output: `routine_only projection_hook_gate False`.
   - A Routine-only manifest can complete without invoking the projector.

5. Ringside cannot display Routine/Goal references because the run-state payload does not carry them.
   - Evidence: `/Users/hermes/ringer/ringer.py:1206-1235` omits them, and Ringside render ranges show no UI for them.

6. The required template is absent.
   - Evidence: exact path test printed `MISSING`.
   - The available demo manifest is not at the required path and only has 2 tasks.

7. No executed end-to-end Routine-reference pass was observed.
   - Existing run-state searches did not find a real completed `routines-goals-demo` run.
   - I did not create one because that would violate the read-only constraints for this audit.

## Risks

- False acceptance risk: `routine_id` and `goal_id` can appear in JSON and documentation while being silently dropped by the parser.
- Source/runtime drift risk: the installed hook has more behavior than the checkout, so future installs or clean hosts would lose Routine/Goal projection.
- Routine-only loss risk: tasks linked only to Routine/Goal IDs do not trigger projection.
- UI observability risk: Ringside operators cannot see Routine/Goal references even if a manifest includes them.
- Paperclip stability caveat: the issue fetch succeeded once, then later HTTP requests to `127.0.0.1:3100` failed with curl exit 7 despite a listener on port 3100.
- Dirty-tree caveat: the checkout has modified tracked files and substantial untracked work. Some relevant files, including `swarms/routines-goals-demo/manifest.json`, are untracked and may represent concurrent work rather than committed source.

## Recommended Next Gate

Do not declare JAC-3487 complete. The next gate should require a clean, source-backed implementation that:

1. Adds explicit `routine_id` and `goal_id` fields to `TaskSpec`, including string and UUID-shape validation or a documented accepted ID contract.
2. Serializes those fields into run state so Ringside and hooks can consume them.
3. Updates `/Users/hermes/ringer/hooks/paperclip_projector.py` source, not only `~/.ringer/hooks`, and makes install/update paths keep the installed hook in sync.
4. Changes `_run_projection_hook()` to trigger for `paperclip_issue`, `bead_id`, `routine_id`, or `goal_id`.
5. Adds `/Users/hermes/ringer/templates/routine-fanout.json` with 3 tasks and Routine/Goal references.
6. Adds Ringside rendering for Routine/Goal references in expanded task view.
7. Runs a real end-to-end Routine-reference Ringer run and captures the run-state path, hook output, projected Paperclip comments, and Ringside evidence.
8. Re-runs the gate from a documented git state, explicitly accounting for the dirty-tree/concurrent-work caveat.

READ_ONLY verdict: NOT READY.
