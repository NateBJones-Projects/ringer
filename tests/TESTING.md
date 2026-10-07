# Test recipes

## `ringer.py ask`

Status: tested. The focused fake-worker regression and the real Codex smoke are
documented below; the real-engine result was verified on 2026-10-01.

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

1. Run the focused regression with isolated Ringer state:

   ```bash
   RINGER_TEST_HOME="$(mktemp -d)"
   RINGER_HOME="$RINGER_TEST_HOME" RINGER_NO_SELF_UPDATE=1 RINGER_NO_CATALOG_REFRESH=1 \
     python3 -m unittest -v tests.test_context_packet tests.test_ask_command
   rm -rf "$RINGER_TEST_HOME"
   ```

   The prompt-aware fake reads the actual task spec. It writes `answer.md` only
   when the trusted packet preamble tells it to save and print the answer. The
   mutation case removes that instruction while leaving `answer.md` in the user
   request; it must fail on the first and only attempt.
2. Create a temporary Markdown source containing a distinctive answer passage.
3. Run `RINGER_NO_SELF_UPDATE=1 python3 ./ringer.py ask "<question>" --source
   <temp-file> --dry-run`.
4. Confirm the packet report names the source passage and stdout says
   `No model call was made.`

Real-engine smoke:

```bash
(
  set -e
  set -o pipefail
  RINGER_SMOKE_HOME="$(mktemp -d)"
  trap 'rm -rf "$RINGER_SMOKE_HOME"' EXIT
  mkdir -p "$RINGER_SMOKE_HOME/state"
  cat >"$RINGER_SMOKE_HOME/config.toml" <<EOF
state_dir = "$RINGER_SMOKE_HOME/state"

[eval]
jsonl_path = "$RINGER_SMOKE_HOME/state/eval.jsonl"

[artifact]
enabled = false

[engines.codex]
bin = "codex"
args_template = ["exec", "--skip-git-repo-check", "{access_args}", "{model_args}", "{engine_args}", "-C", "{taskdir}", "{spec}"]
sandbox_args = ["--sandbox", "workspace-write"]
full_access_args = []
model_default = "gpt-5.6-sol"
EOF
  RINGER_NO_SELF_UPDATE=1 RINGER_NO_CATALOG_REFRESH=1 \
    ./ringer.py ask "Reply with exactly RINGER_ASK_102_OK and no other answer text." \
    --config "$RINGER_SMOKE_HOME/config.toml" \
    --identity codex --engine codex --model gpt-5.6-sol \
    --reasoning-effort low --workdir "$RINGER_SMOKE_HOME/request" \
    | tee "$RINGER_SMOKE_HOME/stdout.txt"
  printf 'RINGER_ASK_102_OK\n' \
    | diff -u - "$RINGER_SMOKE_HOME/request/answer/answer.md"
  test "$(wc -c <"$RINGER_SMOKE_HOME/request/answer/answer.md" | tr -d ' ')" = 18
  rg -F 'RINGER_ASK_102_OK' "$RINGER_SMOKE_HOME/stdout.txt" \
    "$RINGER_SMOKE_HOME/request/answer/worker.log"
  rg -F 'answer                   pass     PASS            1' \
    "$RINGER_SMOKE_HOME/stdout.txt"
)
```

`RINGER_HOME` alone does not override `state_dir` or `eval.jsonl_path` from an
installed config, so this recipe supplies both in a temporary config. It uses
the user's authenticated Codex CLI and spends one model call; the config
contains no secret.

Verified evidence (2026-10-01): the real `gpt-5.6-sol`/low smoke passed in one
attempt. `answer.md` was 18 bytes and contained exactly `RINGER_ASK_102_OK\n`;
the same answer was printed, the worker log contained it, and the summary
reported `pass`, `PASS`, and one attempt.

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
