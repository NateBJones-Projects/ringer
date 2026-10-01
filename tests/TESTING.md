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
