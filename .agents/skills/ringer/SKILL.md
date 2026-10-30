---
name: ringer
description: >-
  Orchestrate implementation and evaluation work through Ringer verified
  worker swarms. Use before drafting or running a Ringer manifest, delegating
  repeatable edits or reviews, choosing Claude/Codex/OpenCode/Grok workers,
  or diagnosing a failed Ringer run. Skip for read-only exploration, pure
  conversation, and a single one-shot edit that does not start an edit/test loop.
---

# Ringer orchestrator playbook for Codex

You are the decision-maker. Ringer workers are the implementation and review
lanes. Keep planning, task boundaries, check design, result review, and final
synthesis in the parent Codex thread; give mechanical or parallelizable work to
Ringer.

When the user requests Sol Advanced, use GPT-5.6 Sol as the parent orchestrator.
In the Codex app, choose Sol and an appropriate reasoning level under Advanced.
For CLI sessions, the equivalent quality-first starting point is:

```bash
codex -m gpt-5.6-sol -c model_reasoning_effort=high
```

`Advanced` is a product selector, not a value for `model_reasoning_effort`.
Raise or lower the effort only when task difficulty warrants it.

## Operating contract

1. Read `templates/README.md` before inventing a swarm shape.
2. Keep each worker task bounded, stateless, and independently verifiable.
3. Put instructions in `spec`; do not make the worker chase an instruction file.
4. Give concurrent editing workers disjoint file ownership, normally through
   worktrees.
5. Write a check that executes the artifact and prints a useful reason on
   failure. `expect_files` is only a fast existence preflight.
6. Run `./ringer.py lint MANIFEST` before spending worker tokens.
7. Use `./ringer.py run MANIFEST --baseline` when a check can be proved against
   the untouched tree.
8. Run the real swarm with an explicit identity, for example:

   ```bash
   ./ringer.py run MANIFEST --identity sol-advanced
   ```

9. Review the run state, raw logs for failures or retries, and at least one
   passing artifact. A worker's summary is never proof.
10. Integrate worker patches serially and run the repository's full validation
    after integration.

Keep Ringside visible for interactive work. Do not use `--no-dashboard` unless
the user asks for headless execution or the run is an automated test.

## Worker routing

The manifest selects a worker with `engine` and, where supported, `model`.
Ringer applies the same closed-stdin, timeout, retry, raw-log, and executed-check
rules to every engine.

- `claude`: Claude Code's non-interactive worker lane. The built-in engine pins
  `claude-sonnet-5`, enables Claude's native task-directory sandbox, refuses to
  fall back to unsandboxed Bash, and streams raw JSON events. Use `engine_args`
  such as `["--effort", "low"]` or `["--effort", "high"]` per task.
- `codex`: the default Codex CLI worker lane. Do not confuse this with the parent
  Sol orchestrator; it is just another worker when selected in a manifest.
- `opencode`: the OpenRouter harness for third-party models. Put the model slug
  in the manifest `model` field.
- `grok`: the Grok Build CLI lane when configured.

Prefer Claude or another cheaper/proven lane for tightly specified typing,
tests, mechanical changes, and independent reviews. Keep architecture choices,
cross-task dependencies, conflict resolution, and acceptance judgment with Sol.
Use `./ringer.py models --task-type TYPE` and local evidence when choosing among
available workers; do not assume one model is best for every task shape.

Example task:

```json
{
  "key": "claude-tests",
  "engine": "claude",
  "model": "claude-sonnet-5",
  "engine_args": ["--effort", "medium"],
  "task_type": "test-hardening",
  "spec": "You own only tests/test_widget.py. Add the named regression tests, do not edit production code, run python -m unittest tests.test_widget, and leave changes uncommitted.",
  "check": "python -m unittest tests.test_widget || { echo 'FAIL: widget regression tests did not pass'; exit 1; }",
  "verified": "The widget regression suite executes and passes"
}
```

## Safety boundaries

- The built-in Claude sandbox is fail-closed on macOS, Linux, and WSL2. If the
  platform cannot start it, the worker must fail setup rather than run loose.
- `full_access: true` is exceptional and still requires `allow_full_access =
  true` in Ringer config. Use it only when the task genuinely needs to spawn
  its own processes outside the task boundary.
- Worktree PASS cleanup removes the worktree. Export patches and any ignored
  deliverables outside the worktree in the check before it exits zero.
- Do not let review workers fix what they discovered. Sol confirms findings,
  then creates a separate fix swarm.

## Completion report

Return the plan chosen, worker/model routing, checks executed, attempt/retry
results, artifacts or patches produced, integration validation, and any
remaining uncertainty. Separate verified facts from worker claims.
