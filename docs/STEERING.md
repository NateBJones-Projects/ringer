# Steering profiles

Steering profiles let a Ringer install keep model-specific operating knowledge beside the orchestrator. Ringer injects qualifying worker rules at the code path that builds each worker prompt, so attempt 1 and the retry use the same deterministic contract. It surfaces driver rules to the orchestrator before a run and appends one observation row after every attempt as a side effect.

Steering is optional and local. It does not replace the manifest, the executed check, or the normal eval log.

## Enable steering

Create a steering directory with a `profiles/` subdirectory, then set it in `config.toml`:

```toml
[steering]
dir = "~/.ringer/steering"
inject_candidates = true
```

`RINGER_STEERING_DIR` overrides `steering.dir` when set. Paths expand `~`. `inject_candidates` defaults to `true`; set it to `false` to omit candidate rules from worker prompts while still injecting confirmed and stale-pending-reverify worker rules.

The configured directory has this shape:

```text
~/.ringer/steering/
├── profiles/
│   ├── gpt-5.6-sol.md
│   └── openrouter-z-ai-glm-5.2.md
└── observations/
    └── ringer/
        └── 2026-07-10.jsonl
```

Ringer creates `observations/ringer/` when it has an observation to write.

## Install and upgrade

Every agent or Ringer install must create and own its own steering directory. Do not point one install at another person's directory or at a shared mutable profile tree. A sane default is:

```bash
mkdir -p ~/.ringer/steering/profiles
```

Then copy or author the profiles that this install should use and configure `dir = "~/.ringer/steering"`. On upgrade, keep that directory in place; it is install data, not part of the Ringer clone. Review profile format changes before replacing local profiles, and preserve the local observations unless you intentionally want a new evidence history.

## Profile resolution

Ringer resolves a task's model in the same order used by its attempt log:

1. The task's `model` field.
2. The engine's `model_default`.
3. A `-m`, `--model`, or `--model=...` value in the composed worker command.

For `openrouter/z-ai/glm-5.2`, it checks these files in order:

1. `profiles/openrouter-z-ai-glm-5.2.md`
2. `profiles/glm-5.2.md`

The first existing path wins. Profile filenames and matching are lowercase.

## Profile file format v1

There is one Markdown file per model: `profiles/<model-slug>.md`. The slug is canonical: it is the filename and the prefix for fully qualified rule IDs such as `gpt-5.6-sol/spec-as-file`.

A profile contains:

1. YAML-like frontmatter with profile metadata.
2. One `## R<n> · <rule-id>` section per rule.
3. Optional `## Environment notes` and `## Changelog` sections.

Ringer uses a tolerant stdlib-only line parser, not a general YAML parser. It reads `model` and `profile_version` from frontmatter. From each rule's first fenced `yaml` block it reads the simple top-level `id`, `status`, and `audience` values. It reads the paragraph beginning with `**Inject:**` through the first blank line. A rule it cannot parse is skipped.

### Frontmatter

```yaml
---
kind: steering-profile
format: 1
model: gpt-5.6-sol
model_names: ["GPT-5.6 Sol (high reasoning)"]
surfaces: ["codex-cli 0.144"]
profile_version: 0.1.0
updated: 2026-07-10
verified_against: gpt-5.6-sol
---
```

`kind` is the constant `steering-profile`. `format` is this format's major version. `model` is the canonical slug. `profile_version` follows semantic versioning:

- MAJOR: a format-breaking change.
- MINOR: a rule addition or rule-status change.
- PATCH: wording, metadata, or evidence-link changes without a status change.

Seed profiles start at `0.1.0`; the first ablation pass that grades the rules promotes the profile to `1.0.0`.

### Rule sections

The heading is `## R<n> · <rule-id>`. A rule number is assigned once, never renumbered, and never reused. Refuted rules remain in the profile because a failed hypothesis is evidence.

````markdown
## R1 · spec-as-file

```yaml
id: spec-as-file
status: candidate
audience: driver
domains: [cad-3d]
first_observed: 2026-07-10
last_verified: 2026-07-10
verified_on: gpt-5.6-sol
steer_gain: null
evidence:
  - kind: observation
    ref: observations/2026-07-10-example.md
    date: 2026-07-10
```

**Inject:** Put every hard constraint in a spec file the model must read.

**Detail:** Explain how the rule works and the failure mode it prevents.

**Evidence notes:** Add optional context beyond the evidence links.
````

`Inject` is one prompt-ready sentence or short paragraph. It must be self-contained, written as an instruction to the agent that receives it, and must not depend on access to the profile repository.

### Audience

Every rule targets one audience:

- `driver`: guidance for the orchestrator steering the model—how to structure a spec, present references, or phrase feedback. Ringer prints these before running tasks, including during `--dry-run`. Driver rules are never pasted into a worker prompt. If `audience` is absent, Ringer defaults it to `driver`.
- `worker`: text written for the model itself. Ringer may prepend it to the worker spec according to the rule status.

The distinction keeps "how to prompt this model" separate from "what to tell this model."

### Status lifecycle

```text
candidate --ablation--> confirmed --model version bump--> stale-pending-reverify
    |                       ^                                  |
    +------ablation------> refuted        re-verification -----+
```

- `candidate`: a hypothesis awaiting an ablation.
- `confirmed`: a validated rule.
- `refuted`: a tested rule that did not help; it remains in the profile but is never injected.
- `stale-pending-reverify`: a formerly confirmed rule awaiting validation on a newer model version.

Only the validation gate—the steering-foundry or a manually run ablation—changes a rule's status. A model-version bump changes confirmed rules to stale-pending-reverify. Agents append observations; they never edit profiles or promote, refute, or reverify rules. An N=1 observation may become an evidence link later, but it cannot change status.

### Environment notes and changelog

An optional `## Environment notes` section records stamped, verifiable tooling or surface facts that steering agents need but that are not steering hypotheses. Examples include CLI flags, sandbox behavior, or version-specific limitations. Each bullet ends with `— <surface>, <date>`. These facts do not graduate through rule statuses; they are simply current or outdated. Renderers may append them when the target surface matches, but Ringer's worker injector does not currently emit them.

An optional `## Changelog` records profile-version changes. Keep status changes, additions, staleness updates, and wording/evidence edits aligned with the semantic-version rules above.

### Injection contract

Worker rules are emitted in profile order:

- `confirmed`: `- <inject text>`
- `candidate`: `- (candidate) <inject text>`, unless `inject_candidates = false`
- `stale-pending-reverify`: `- (unverified on current model version) <inject text>`
- `refuted`: never injected

The complete worker block is:

```text
[Steering profile <model> v<profile_version> — auto-injected by ringer.py]
- <confirmed inject text>
- (candidate) <candidate inject text>
- (unverified on current model version) <stale inject text>
[End steering profile]

<original task spec>
```

If no worker rules qualify, Ringer does not add an empty block.

Driver rules with status `confirmed`, `candidate`, or `stale-pending-reverify` are printed once per distinct resolved model:

```text
Steering notes for <model> (v<profile_version>) — apply when writing specs/feedback:
- (<status>) <inject text>
```

## Observation JSONL

After each attempt's verdict is known, Ringer appends one JSON object to:

```text
<steering-dir>/observations/ringer/<YYYY-MM-DD>.jsonl
```

The filename uses the UTC date. Each row contains:

| Field | Type | Meaning |
|---|---|---|
| `ts` | string | UTC ISO timestamp |
| `source` | string | Always `ringer.py` |
| `run_id` | string | Ringer run ID |
| `run_name` | string | Manifest run name |
| `task_key` | string | Manifest task key |
| `task_type` | string | Optional task classification |
| `engine` | string | Configured worker engine |
| `model` | string | Resolved model |
| `profile` | string or null | Matched profile filename slug |
| `profile_version` | string or null | Matched profile version |
| `rules_injected` | array of strings | Worker rule IDs injected for this attempt |
| `attempt` | integer | Attempt number, starting at 1 |
| `retry` | boolean | Whether this was the retry |
| `verdict` | string | `PASS`, `FAIL`, `TIMEOUT`, or `ERROR` |
| `duration_ms` | integer | Attempt duration in milliseconds |
| `worker_tokens` | integer or null | Tokens reported for this attempt |
| `check_excerpt` | string | First 500 characters of raw check output |

Observation rows are evidence inputs only. Writing one never changes a profile.

## Fail-open guarantee

The entire steering feature is fail-open. With steering unconfigured, Ringer follows its original prompt, log, and state paths without a steering branch. A missing directory, missing profile, unreadable file, malformed or empty profile, rule parse error, directory-creation failure, observation write failure, or any other steering exception never fails, blocks, delays, retries, or otherwise changes the run. The only allowed effect is that no steering is injected or no observation is recorded. Observation-write failures are noted in the task worker log with the `[ringer.py] steering:` prefix when that log remains writable.

The worker's stdin remains closed, sandbox selection remains explicit, verification still executes the artifact, and raw worker output remains in the worker log.

## Verification recipe

Status: tested by the repo steering test suite.

Safe actions: all tests use temporary homes, configs, profiles, work directories, and the offline mock engine. They do not call a model API.

Run from the Ringer repository root:

```bash
python3 -m unittest tests.test_steering -v
python3 -m unittest discover -s tests
python3 -c "import ast; ast.parse(open('ringer.py').read())"
```

No cleanup is required; temporary test directories are removed by `unittest`.
