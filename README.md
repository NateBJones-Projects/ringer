# Ringer

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

```bash
git clone https://github.com/NateBJones-Projects/ringer && cd ringer
cp config.sample.toml ~/.config/ringer/config.toml   # optional — sane defaults without it
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

### Manifest fields

| Field | What it does |
|---|---|
| `key` | Task name — becomes the working subdirectory and the label everywhere |
| `spec` | The prompt handed to the worker |
| `check` | Shell command run after the worker exits; exit 0 = PASS |
| `expect_files` | Files that must exist and be non-empty before the check runs |
| `engine` | Which configured engine runs this task (default `codex`) |
| `timeout_s` | Per-task kill timer (default 900) |
| `full_access` | Worker runs unsandboxed — required for workers that spawn their own sub-workers; must also be enabled in config |
| `worktrees` (run-level) | Give each task an isolated git worktree of `repo` so parallel workers can't collide |

## Engines are pluggable

Codex is built in. Anything with a headless CLI is a config block away:

```toml
[engines.mymodel]
bin = "/usr/local/bin/mycli"
args_template = ["run", "{spec}", "--dir", "{taskdir}"]
```

Per-task `"engine": "mymodel"` routes work to it. `config.sample.toml` ships commented examples for Grok and OpenCode-style setups — the invariants (stdin closed, process-group kill, executed verification, raw logs) apply to every engine identically.

## Ringside — mission control

A native HUD (Tauri — one codebase for macOS, Windows, Linux) that floats above your work: one section per live swarm with a color-coded identity badge, per-task status chips, elapsed clocks, token burn, and a distinct state for swarms whose orchestrator *died* versus finished — the failure mode every dashboard forgets.

Multiple swarms at once is the designed-for case. Run three batches under three identities and Ringside shows all three, color-separated, live.

```bash
cd hud
cargo tauri build     # needs Rust + the Tauri CLI (cargo install tauri-cli)
```

The bundle lands in `hud/target/release/bundle/`. Ringer auto-opens Ringside when installed; `--browser` falls back to the localhost dashboard, and `--no-dashboard` runs headless.

## The eval loop

Every worker attempt — pass, fail, timeout, retry — is logged with its spec, engine, duration, token count, and the raw check output. Local JSONL by default; point `[eval.postgres]` at a database to aggregate across machines. Failure rows are the point: they tell you which spec styles, engines, and task shapes actually work, so the swarm gets better on evidence instead of vibes.

## Hard-won invariants

Four rules are baked into every worker invocation. They all cost us real debugging hours; you get them for free:

1. **stdin is always closed** (`< /dev/null`) — headless CLI agents hang forever waiting on a TTY that isn't there.
2. **Sandbox mode is always explicit** — default sandboxes silently resolve to read-only in temp directories and block every artifact write.
3. **Verification executes the artifact** — an agent's own "done" is not evidence. Exit codes are.
4. **Raw output only** — logs and eval rows carry verbatim worker output, never a summary. Anything that needs judgment reads the raw data.

## Requirements

- Python 3.11+ (stdlib only; `psycopg` needed only for the optional Postgres eval backend)
- At least one agent CLI (Codex works out of the box)
- Rust toolchain, only if you're building Ringside from source

---

Built by [Jon Edwards](https://limitededitionjonathan.com) and his agent fleet — a Claude orchestrator wrote the specs and reviewed the diffs, Codex swarms wrote the implementation, and this repo's own eval table caught its first three bugs. The tool is its own proof of concept.
