# Test recipes

## `ringer.py ask`

Status: tested

Purpose: verify context-packet selection, one-worker execution, opt-in request
redaction, Ringside state, artifact registration, and the one-attempt contract.

Safe actions:

- Run the unit suite; worker tests use temporary directories and local Python
  fixture workers.
- Run `ask --dry-run` against temporary text or Markdown sources.

Unsafe actions:

- Do not omit `--dry-run` from a smoke command unless a real model call is
  intended.

Verification steps:

1. Run `RINGER_NO_SELF_UPDATE=1 python3 -m unittest discover -s tests`.
2. Create a temporary Markdown source containing a distinctive answer passage.
3. Run `RINGER_NO_SELF_UPDATE=1 python3 ./ringer.py ask "<question>" --source
   <temp-file> --dry-run`.
4. Confirm the packet report names the source passage and stdout says
   `No model call was made.`

Cleanup:

- Remove the temporary source and generated request directory when one was
  supplied explicitly.

Known test-environment constraint:

- Worker tests mock only the dashboard socket bind because restricted test
  sandboxes can reject local listeners. They assert that the run records a
  dashboard port and enters the artifact library.

## Ringside operator views

Status: tested (2026-10-01)

Purpose: verify the approved Figma direction with real local API contracts:
Runs and task selection, retry/check evidence, Outputs and saved versions,
Models and sample counts, compact/expand, reconnect and empty states.

Safe actions: run the Python suite and dependency-free Node checks. The preview
command below creates its own temporary state, model database, active-run registry
and logs. It launches no workers and never reads or modifies real run history.

1. Run `RINGER_NO_SELF_UPDATE=1 python3 -m unittest discover -s tests -v`.
2. Run `node tests/ringside_frontend_test.js` (Node is optional for the Python suite).
3. Run `RINGER_NO_SELF_UPDATE=1 python3 tests/ringside_preview.py --port 8766`.
4. Open the printed URL. Select the retrying task: its failed check must remain
   visible. Open logs, then return to the same task. Search for a task and switch
   runs. A stopped run must not show a worker as still working.
5. Open its result, select a saved version, and wait through two refreshes. The
   saved preview must stay selected. Search outputs and switch between them.
6. Open Models, filter a task type and expand all signals. Check that the task and
   attempt counts remain visible, a two-task model says Low sample, and judgment
   notes are escaped as text. No fixed date filter or invented score is displayed.
7. Enter compact mode, expand, and test a narrow browser. Use Tab/Enter to select
   tasks. Open Settings and pause/resume auto-refresh.
8. Run another preview with `--port 8767 --empty`; all three views must explain
   the empty state. Stop the populated preview and verify the page shows a
   reconnect notice while retaining the last results.

Cleanup: Ctrl-C each preview process; its temporary directory is removed.
No Finder folders should be opened from the preview unless intentionally testing
that action. The selected-folder route is covered with a mocked OS opener.

Native build: `PATH="$HOME/.cargo/bin:$PATH" cargo tauri build --bundles app`
from `hud/`. The sync script must byte-copy `dashboard/ringside.html`,
`ringside.css`, `ringside.js`, assets, and `hud/frontend/hud.js` into `hud/dist/`.
Compare the release binary to the bundle executable. The shared frontend's native
transport/compact behavior has Node coverage; the native app was built, not
interactively launched (the browser is Ringer's primary operator interface).
