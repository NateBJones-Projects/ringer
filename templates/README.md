# Ringer Template Library

A kit is a reusable Ringer starter: a manifest skeleton, check skeletons, and a short README that capture one proven or planned task shape. At manifest-writing time, make one of three sanctioned moves: choose one kit and fill in the placeholders; mix and match by lifting a round, task shape, or check from one kit into another; or roll your own when nothing fits. Task specs and checks are designed to be lifted whole, so prefer moving complete pieces over rewriting them from memory. If you roll your own, skim the two nearest kits first so your manifest still inherits the prior art.

## Catalog

| Kit | What it does | Reach for it when | Status |
|---|---|---|---|
| `review-swarm` | Runs N read-only scouts, one surface each, with structured findings. | You need broad review coverage before deciding what to fix. | Proven in a recorded run |
| `fix-swarm` | Runs N isolated workers in worktrees to apply independent fixes, then exports patches. | You have confirmed fixes that can be split by file or surface. | Proven in a recorded run |
| `focus-group` | Runs isolated persona workers against a product, pitch, prompt, or workflow and collects parseable verdicts. | You need product or messaging feedback without persona bleed. | Proven in a recorded run |
| `bakeoff` | Runs scenarios by candidate models through the per-task `model` field. | You need evidence for choosing a model, prompt, or configuration. | Proven in a recorded run |
| `research-with-proof` | Runs research tasks plus a proof task whose check executes the claim. | You need the final answer to be verifiable, not just plausible. | Proven in a recorded run |
| `launch-kit` | Runs three go-to-market rounds: research/candidates/build, persona panel, then final assembly. | You need a launch package from rough direction to assembled output. | Proven in a recorded run, 2026-07-06 |
| `asset-swarm` | Runs media production lanes with render-as-check animations, idempotent image batches, diagrams, and captures. | You need many visual or media assets produced with executable validation. | Proven in a recorded run, 2026-07-06 |
| `adversarial-review` | Sends the same artifact to N different models, collects structured findings, then synthesizes. | You want model diversity on one artifact before deciding what is real. | Proven in a recorded run |
| `repo-feature` | Lets sandboxed workers edit a real repo through `writable_roots`, then runs a build check and git-porcelain allowlist. | You know what to build and need a delivery lane into an actual repo. | Proven in a recorded run, 2026-07-06 |
| `migration-swarm` | Splits mechanical codebase transforms across worktrees and exports patches. | You have repetitive edits that can be partitioned safely. | Blueprint |
| `doc-swarm` | Assigns documentation by module with executed examples and no-invented-API checks. | You need docs that prove the APIs and commands they describe. | Blueprint |
| `test-hardening` | Adds tests by module with count-increase and assertion-density checks while keeping `src/` off-limits. | You need stronger tests without letting workers change production code. | Blueprint |
| `competitive-teardown` | Runs N scouts by competitor with verbatim-citation allowlist checks and a synthesis phase. | You need grounded competitor analysis with traceable source material. | Blueprint |
| `data-pipeline` | Splits fetch, transform, and validate stages with executed validators and honesty rules. | You need a data workflow that proves each stage produced what it claims. | Blueprint |
| `probe` | Provides a one-task manifest for smokes, probes, and post-mortems. | You need a visible, logged check before trusting a new engine, model, harness, or diagnosis. | Proven as a practice |

## Kit Anatomy

Standard kit files are `manifest.json`, `README.md`, and usually one or more executable helpers under `checks/`. Placeholders use bare identifiers like `{{NAME}}` for required values and freeform prompts like `{{THING — inline guidance}}` when the placeholder itself explains the choice to make. After fill-in, every manifest must pass `./ringer.py lint` before a run. Kit checks are skeletons to adapt to the real task; never turn them into `exit 0`, `true`, or any other check that cannot fail.

## Composition Guide

- `review-swarm` findings feed `fix-swarm`: scouts identify the confirmed work, then fix workers own disjoint patches.
- `launch-kit` round 2 is `focus-group` specialized: use the same isolated persona pattern anywhere a draft needs reaction.
- `asset-swarm` lanes drop into any kit that needs media: images, animations, diagrams, and captures can be validated as task outputs.
- `probe` is the pre-flight for any new engine or model: prove the harness path once before giving it a real batch.
- `repo-feature` is the delivery lane after research kits decide what to build: keep discovery separate from repo mutation.

## Craft Floor

Every spec opens with the worker's role and boundary, names what it owns, and states what it must not touch. Checks print why they fail, not just that they failed. Declare deliverables in `expect_files` so the artifact page has the real outputs. When worktrees are doomed after success, deliverables and patch exports land outside those worktrees before the check passes.
