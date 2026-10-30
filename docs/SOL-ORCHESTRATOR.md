# Sol Advanced orchestrator, heterogeneous workers

This is the supported operating model for using GPT-5.6 Sol as Ringer's
decision-maker while Claude Code, Codex CLI, Grok Build, OpenCode/OpenRouter,
and future CLI agents do bounded worker tasks.

## Architecture

```text
human brief
    |
    v
Codex app or CLI: GPT-5.6 Sol + High reasoning
    |  plan, partition, choose workers, write checks, review evidence
    v
manifest.json
    |
    v
ringer.py
    |  isolate, launch, time out, capture raw output, verify, retry once
    +-------------------+-------------------+-------------------+
    |                   |                   |                   |
Claude Code         Codex CLI          OpenCode/Grok       future CLI
task directory      task directory     task directory      same contract
    |                   |                   |                   |
    +-------------------+-------------------+-------------------+
                            |
                            v
             executed checks + run state + Ringside
                            |
                            v
                 Sol reviews and integrates
```

Sol remains outside `ringer.py`. That is intentional. Ringer's runtime is the
deterministic control plane for process isolation and evidence; the premium
model is the adaptive control plane for judgment. Putting Sol into the worker
loop would blur the boundary between the agent that produced a result and the
agent that accepts it, and it would make Ringer responsible for one vendor's
conversation/session protocol.

The Codex-facing integration is a repo/user skill. Codex discovers repo skills
under `.agents/skills`, while `AGENTS.md` and project config remain available
for narrower repository conventions. See OpenAI's [Codex skills
documentation](https://developers.openai.com/codex/skills) and [configuration
reference](https://developers.openai.com/codex/config-reference).

## What is implemented

- `.agents/skills/ringer/SKILL.md` gives Codex/Sol the Ringer orchestration
  workflow, worker-routing rules, safety boundaries, and completion contract.
- `install-agent` installs Codex and Claude host support by default. Use
  `--target codex` or `--target claude` for only one host.
- `claude` is a built-in worker engine. A manifest can select it without adding
  a custom engine block.
- The Claude lane pins `claude-sonnet-5`, accepts per-task `--effort`, uses
  non-interactive streaming output, and disables session persistence.
- Sandboxed Claude workers enable Claude Code's native filesystem/network
  sandbox, auto-approve commands only inside it, disable the unsandboxed escape
  hatch, and fail setup if the sandbox is unavailable.
- `full_access: true` switches Claude to bypass mode only behind Ringer's
  existing `allow_full_access` configuration gate.
- Model identity records Claude separately from its harness so Ringside and the
  local scoreboard do not label the harness as the model.

Claude's non-interactive flags and model selection are documented in the
[Claude Code CLI reference](https://code.claude.com/docs/en/cli-usage). Its
native sandbox behavior and prerequisites are documented in [Claude Code
sandboxing](https://code.claude.com/docs/en/sandboxing).

## One-time setup

Ringer itself runs on macOS/Linux, or Windows through WSL. In the Ringer clone:

```bash
# Orchestrator
npm install -g @openai/codex
codex login

# Primary worker lane
npm install -g @anthropic-ai/claude-code
claude auth login

# Install both Ringer host skills; Claude also receives the existing nudge hooks
./ringer.py install-agent

# Or install only the Codex/Sol orchestration skill
./ringer.py install-agent --target codex
```

On WSL2, install Claude Code's sandbox prerequisites before using the Claude
lane:

```bash
sudo apt-get install bubblewrap socat
```

The built-in worker deliberately refuses to run sandboxed tasks if those
prerequisites are missing. WSL1 does not provide the required isolation; use
WSL2 or choose another sandboxed engine.

To start the orchestrator in the Codex CLI:

```bash
codex -m gpt-5.6-sol -c model_reasoning_effort=high
```

In the Codex app, open the Advanced model controls and select GPT-5.6 Sol with
High reasoning. `Advanced` is the selector name, not a reasoning-effort value.
Start with High for architecture and acceptance work; lower it when the task is
already tightly specified.

Then invoke the skill explicitly or describe a task that matches it:

```text
$ringer Use Sol as orchestrator. Plan this feature, assign bounded implementation
and test tasks to Claude workers, use another configured engine for one
independent review, execute every check, then review and integrate the evidence.
```

## Manifest contract

The orchestrator chooses a worker per task. This example assigns implementation
to Claude, a separate review to another configured model, and keeps acceptance
with Sol:

```json
{
  "run_name": "widget-cache",
  "workdir": "/tmp/widget-cache",
  "repo": "/absolute/path/to/widget-repo",
  "worktrees": true,
  "max_parallel": 2,
  "tasks": [
    {
      "key": "claude-cache-fix",
      "engine": "claude",
      "model": "claude-sonnet-5",
      "engine_args": ["--effort", "medium"],
      "task_type": "code-fix",
      "spec": "You own only src/cache.py and tests/test_cache.py in this dedicated worktree. Implement the approved cache invalidation rule. Do not commit or edit any other file. Run python -m unittest tests.test_cache. Leave changes uncommitted so the check can export the patch.",
      "check": "python -m unittest tests.test_cache || { echo 'FAIL: focused cache tests failed'; exit 1; }; git add src/cache.py tests/test_cache.py || { echo 'FAIL: could not stage owned files'; exit 1; }; git diff --cached --quiet && { echo 'FAIL: worker produced no patch'; exit 1; }; git diff --cached > /tmp/widget-cache/claude-cache-fix.patch; test -s /tmp/widget-cache/claude-cache-fix.patch || { echo 'FAIL: patch export is empty'; exit 1; }",
      "expect_files": [],
      "verified": "The focused cache tests pass and a non-empty scoped patch is exported"
    },
    {
      "key": "independent-review",
      "engine": "opencode",
      "model": "openrouter/z-ai/glm-5.2",
      "task_type": "code-review",
      "spec": "Read the supplied design and repository sources without editing them. Write only report.md in the current task directory. Report concrete cache correctness risks with file-and-line evidence, or write NO FINDINGS and list what you inspected.",
      "check": "python /absolute/path/to/checks/review-swarm.py report.md || { echo 'FAIL: review report did not satisfy the evidence contract'; exit 1; }; cp report.md /tmp/widget-cache/independent-review-report.md || { echo 'FAIL: could not export review before worktree cleanup'; exit 1; }; test -s /tmp/widget-cache/independent-review-report.md || { echo 'FAIL: exported review is empty'; exit 1; }",
      "expect_files": ["report.md"],
      "verified": "The review report satisfies the structured evidence contract and is exported before worktree cleanup"
    }
  ]
}
```

The example is a skeleton: replace the paths and review validator with real
ones, then run `lint` and `--baseline`. The orchestrator must inspect exported
patches before applying them. Do not let the same worker both discover a defect
and approve its own fix.

## Routing policy

Use role separation, not a permanent model hierarchy:

| Work | Owner | Reason |
|---|---|---|
| Ambiguous decomposition, dependencies, acceptance criteria | Sol | Requires global context and judgment |
| Check design and baseline interpretation | Sol | Defines what Ringer is allowed to believe |
| Tightly bounded implementation and tests | Claude or another proven worker | Mechanical work is isolated and verified |
| Independent review | A different worker/model | Reduces correlated blind spots |
| Conflict resolution and patch integration | Sol | Requires cross-worker synthesis |
| Final full-suite validation | Ringer command run by Sol | Acceptance rests on executed evidence |

Before the first run of a task shape, inspect the local evidence:

```bash
./ringer.py models --task-type code-fix
./ringer.py models --explore --task-type code-fix
```

The model that is cheaper in general is not automatically cheaper per passing
task. Prefer first-try pass rate and the actual check contract over vendor or
model reputation.

## Rollout plan and acceptance gates

1. **Host integration**: install only the Codex skill in a temporary home and
   verify no Claude settings are written. Install `all` and verify both skills
   plus Claude's two existing nudge hooks. Reinstall to prove idempotence.
2. **Worker composition**: dry-run a one-task Claude manifest and inspect the
   argv. It must contain print mode, stream JSON, the pinned model, the explicit
   sandbox settings, and closed stdin through Ringer.
3. **Sandbox smoke**: run a Claude task that writes inside its task directory
   and attempts a harmless write outside it. The inside write must succeed; the
   outside write must fail without an approval prompt or unsandboxed retry.
4. **Verification smoke**: make attempt one fail with an explanatory check,
   confirm the check output enters attempt two, and confirm only an executed
   exit-zero check yields PASS.
5. **Mixed swarm**: run at least one Claude implementation task and one
   different-engine review task. Confirm Ringside identifies harness, model,
   attempt, and evidence separately.
6. **Integration gate**: have Sol inspect and apply one exported patch at a time,
   run the full repository suite after each application, and reject any worker
   claim that the checks do not support.

The codebase's unit tests cover gates 1 and 2 without spending model tokens.
Gates 3 through 6 are live acceptance tests because they depend on local CLI
authentication, platform sandbox support, and paid model execution.

## Failure policy

- Missing Claude binary: fail before spawning and print the install/login hint.
- Missing sandbox dependency: Claude exits setup; Ringer records a failed
  attempt instead of silently running unsandboxed.
- Worker exits zero but artifact/check fails: retry once with raw failure
  context.
- Worker times out: terminate the process group, log the timeout, then use the
  normal retry policy.
- Two workers touch the same path: the manifest is wrong. Stop and repartition;
  do not resolve the collision by letting them race.
- Review disagrees with implementation: Sol confirms the evidence against the
  repository before opening a fix task.

This keeps the trust boundary simple: workers may propose or type; only checks
prove; Sol decides what the proof means.
