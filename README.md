# Ringer

![Ringer — she reviews; the wall works](docs/hero.png)

**Parallel AI-agent swarms that prove their work. Your expensive model plans and reviews; cheap workers do the typing.**

Frontier models are finally good enough to trust with real implementation — but their tokens are priced like senior-engineer hours, and most of a build is not senior-engineer work. It's scaffolding, migrations, test suites, batch transforms. Mechanical labor.

So split the roles. Your best model writes the specs and reviews the results. A swarm of cheap workers — Codex, Grok, anything with a CLI — does the implementation in parallel. Your premium budget stops scaling with lines of code written and starts scaling with decisions made.

One problem: parallel agents lie. "Done" doesn't mean working. Ringer doesn't take the worker's word for anything — it **executes your check command** against the artifact. Pass or fail is decided by running the code, not by reading the agent's summary. Failures retry once with the failure context injected, and every attempt is logged so your setup gets measurably better over time.

And because a swarm you can't see is a swarm you don't trust: **Ringside**, a native always-on-top HUD that shows every live swarm on your machine — who's running it, what each worker is doing, elapsed time, token burn — in real time.

## How it works

```
manifest.json ──▶ ringer.py ──▶ N parallel workers (codex exec, each in its own dir)
                      │                │
                      │                ▼
                      │         executed checks ── fail ──▶ retry once w/ failure context
                      │                │
                      ▼                ▼
              ~/.ringer/runs/    eval log (JSONL or Postgres)
                      │
                      ▼
                  Ringside HUD (live, all swarms, all identities)
```

## Quickstart

1. Get the repo:

```bash
git clone https://github.com/NateBJones-Projects/ringer && cd ringer
cp config.sample.toml ~/.config/ringer/config.toml   # optional — sane defaults without it
```

2. Teach your agent to route work through Ringer:

```bash
# optional but recommended: teach your agent to route work through ringer
./ringer.py install-agent
```

3. Run the demo:

```bash
./ringer.py demo                                      # 3 real workers, verified end to end
```

The demo spawns three Codex workers in parallel, verifies each artifact by executing it, and prints a verdict table. If you have the [Codex CLI](https://github.com/openai/codex) installed and authenticated, that's the whole setup.

Run your own batch:

```bash
./ringer.py run swarm.json --max-parallel 4
```

```json
{
  "run_name": "my-batch",
  "workdir": "/tmp/my-batch",
  "max_parallel": 3,
  "tasks": [
    {
      "key": "alpha",
      "spec": "Create alpha.txt containing exactly: alpha ready",
      "check": "test \"$(cat alpha.txt)\" = \"alpha ready\"",
      "expect_files": ["alpha.txt"]
    }
  ]
}
```

Each task gets its own directory, its own worker, its own log, and its own verdict. `check` is any shell command — exit 0 is the only thing Ringer believes.

> **Write checks that print why they fail.** A silent `exit 1` (the `git diff --quiet` style) costs you twice: the retry prompt gets no failure context to fix against, and the eval log records an undiagnosable row. `diff` beats `diff -q`; an assert with a message beats a bare test.

**Identity**: runs are stamped with an orchestrator identity (shown in Ringside and eval rows). Resolution order: `--identity` > `FLEET_IDENTITY`/`RINGER_IDENTITY` env > a `.fleet-agent` file found walking up from the working directory (drop one in a repo root to give that repo's swarms their own name) > `identity_default` in config > short hostname.

### Manifest fields

| Field | What it does |
|---|---|
| `key` | Task name — becomes the working subdirectory and the label everywhere |
| `spec` | The prompt handed to the worker |
| `check` | Shell command run after the worker exits; exit 0 = PASS |
| `expect_files` | Files that must exist and be non-empty before the check runs |
| `engine` | Which configured engine runs this task (default `codex`) |
| `model` | Which model a harness engine runs for this task — fills the engine's `{model}` placeholder (e.g. `"openrouter/moonshotai/kimi-k2.7"`); empty uses the engine's `model_default` |
| `timeout_s` | Per-task kill timer (default 900) |
| `engine_args` | Extra CLI flags for this task's worker, spliced in at the engine's `{engine_args}` placeholder — e.g. `["-c", "model_reasoning_effort=low"]` so the orchestrator picks reasoning depth per task |
| `verified` | One plain-English sentence saying what the check proves — shown on the results page next to "finished & checked" |
| `full_access` | Worker runs unsandboxed — required for workers that spawn their own sub-workers; must also be enabled in config |
| `worktrees` (run-level) | Give each task an isolated git worktree of `repo` so parallel workers can't collide |

> **Worktree footgun:** on PASS the task's worktree is removed — including anything written inside it. In worktrees mode, worker logs live outside task worktrees in `workdir/logs/`; have workers write deliverables outside the worktree too, or have your `check` copy artifacts out before it exits 0.

## Lint

Lint checks a manifest for the mistakes that make swarms hard to trust: checks that cannot fail, silent checks, worktree deliverables that disappear, worker commits that die with deleted worktrees, serial fan-out, write collisions, and underspecified specs.

```bash
./ringer.py lint templates/review-swarm.json
lint: clean (1 tasks)
```

`run` and `demo` also print any lint findings as non-blocking warnings after the manifest loads. They teach at the moment of use; they do not stop a run.

A check that cannot fail is trusting the worker with extra steps.

## Make your agent actually use this

Between swarms, agents drift back to invisible inline work. Reminders decay, so enforcement ships with the product.

Run one command:

```bash
./ringer.py install-agent
```

It installs the ringer skill — the orchestrator playbook — user-level for Claude Code, and registers two gentle hooks: a Bash hook that notices model-calling or harness commands running outside a live Ringer run, and an edit-loop hook that notices batch editing without a run. Each hook nudges ONCE per session, pointing the agent at the skill.

The hooks never block anything. A user who says "just do it inline" is obeyed; uninstall with `./ringer.py uninstall-agent`.

For CI and evals, `config.sample.toml` includes `[engines.mock]` so the enforcement stack can be tested without an API bill.

## Engines are pluggable

![Identical workers, each under its own light](docs/engines.png)

Codex is built in. Anything with a headless CLI is a config block away:

```toml
[engines.mymodel]
bin = "/usr/local/bin/mycli"
args_template = ["run", "{spec}", "--dir", "{taskdir}"]
```

Per-task `"engine": "mymodel"` routes work to it. `config.sample.toml` ships commented examples for Grok and OpenCode setups — the invariants (stdin closed, process-group kill, executed verification, raw logs) apply to every engine identically.

### The universal harness: OpenCode + OpenRouter

Unless a model ships its own first-class harness (Codex does), OpenCode is the harness that runs it — one engine block covers every OpenRouter-served model. `config.sample.toml` includes a ready-to-uncomment engine whose `{model}` placeholder is filled per task from the manifest's `"model"` field, with `model_default` as the fallback. The shipped default is OpenRouter's `z-ai/glm-5.2` — roughly $0.74/M input and $2.33/M output (2026-07), about 20-30x cheaper output than frontier coding models; a complete write-code-and-pass-the-check task lands around a penny.

OpenCode ships no OS sandbox, so the engine's `bin` points at an absolute path to `engines/opencode-sandboxed.sh` (ringer does not resolve engine bins relative to the repo): a macOS Seatbelt wrapper that leaves network and reads open but confines writes to the task dir, a per-run scratch dir (wired as the agent's `TMPDIR`/`XDG_CACHE_HOME`), and OpenCode's own state/config dirs. Its `--dangerously-skip-permissions` flag only silences OpenCode's interactive prompts; Seatbelt is the actual containment. Task paths reach the profile as `sandbox-exec -D` parameters rather than string interpolation, so a task dir with quotes or parens can't inject sandbox rules. `--no-sandbox` is wired as the engine's `full_access_args`, so ringer's `allow_full_access` gate still governs escapes. Non-macOS installs need their own sandbox (or full-access mode).

Route with per-task `"engine": "opencode"`, pick the model with per-task `"model": "openrouter/<any-model>"`, and set reasoning effort via `engine_args`: `["--variant", "low|high|max"]`. A sensible split: mechanical or tightly-specced tasks on the cheap lane, gnarly ones on your frontier engine — the executed check catches shortfalls either way, and `swarm_runs` rows tell you whether the cheap lane's pass rate holds.

## Ringside — mission control

![Ringside showing three live swarms under three identities](docs/ringside.png)

A native HUD (Tauri — one codebase for macOS, Windows, Linux) that floats above your work: one section per live swarm with a color-coded identity badge, per-task status chips, elapsed clocks, token burn, and a distinct state for swarms whose orchestrator *died* versus finished — the failure mode every dashboard forgets.

Multiple swarms at once is the designed-for case. Run three batches under three identities and Ringside shows all three, color-separated, live.

```bash
cd hud
cargo tauri build     # needs Rust + the Tauri CLI (cargo install tauri-cli)
```

The bundle lands in `hud/target/release/bundle/`. Ringer auto-opens Ringside when installed; `--browser` falls back to the localhost dashboard, and `--no-dashboard` runs headless.

## The eval loop

![Timed, verified, logged](docs/eval-loop.png)

Every worker attempt — pass, fail, timeout, retry — is logged with its spec, engine, duration, token count, and the raw check output. Local JSONL by default; point `[eval.postgres]` at a database to aggregate across machines. Failure rows are the point: they tell you which spec styles, engines, and task shapes actually work, so the swarm gets better on evidence instead of vibes.

## Hard-won invariants

Four rules are baked into every worker invocation. They all cost us real debugging hours; you get them for free:

1. **stdin is always closed** (`< /dev/null`) — headless CLI agents hang forever waiting on a TTY that isn't there.
2. **Sandbox mode is always explicit** — default sandboxes silently resolve to read-only in temp directories and block every artifact write.
3. **Verification executes the artifact** — an agent's own "done" is not evidence. Exit codes are.
4. **Raw output only** — logs and eval rows carry verbatim worker output, never a summary. Anything that needs judgment reads the raw data.

## License

[PolyForm Shield 1.0.0](LICENSE.md) — free to use, modify, and share, including inside your own commercial work. The one thing you can't do is offer Ringer or Ringside (or a derivative that competes with them) as a product or service of your own. Commercial rights to the tool itself belong to Nate Jones Media LLC.

## Requirements

- Python 3.11+ (stdlib only; `psycopg` needed only for the optional Postgres eval backend)
- At least one agent CLI (Codex works out of the box)
- Rust toolchain, only if you're building Ringside from source

![Between rounds](docs/between-rounds.png)

---

Built by [Jon Edwards](https://limitededitionjonathan.com) and his agent fleet — a Claude orchestrator wrote the specs and reviewed the diffs, Codex swarms wrote the implementation, and this repo's own eval table caught its first three bugs. The tool is its own proof of concept.
