# Adversarial Review

## What it is

A multi-model review swarm: several reviewers inspect the same diff, artifact, or route, each on a different model through one engine's per-task `model` field. Reviewers only report structured findings; the orchestrator later merges, dedupes, and confirms findings against the code before deciding what to fix.

This is a review pattern, not a fix pattern. It is useful because different models miss different issues, but only the orchestrator should decide which findings are real.

## When to use

Use this before merging a risky change, publishing a high-visibility artifact, or accepting generated code where one reviewer is not enough. It is especially useful for auth, billing, data access, product claims, migrations, and UI flows where the cost of a missed issue is higher than the cost of parallel review.

## Fill in

| Placeholder | What goes there |
|---|---|
| `{{ARTIFACT_PATH_OR_DIFF_COMMAND}}` | The exact diff, patch path, route, artifact, or command every reviewer must inspect. |
| `{{CODEBASE_CONTEXT}}` | Short context on the repo, product, users, and relevant architecture. |
| `{{KIT_DIR}}` | Absolute path to `templates/adversarial-review` after copying or installing this kit. |
| `{{MODEL_A}}` | First model slug for the review engine. |
| `{{MODEL_B}}` | Second model slug for the review engine. |
| `{{MODEL_C}}` | Third model slug for the review engine. |
| `{{REVIEW_ENGINE}}` | Engine name that supports the per-task `model` field, often `opencode`. |
| `{{REVIEW_FOCUS}}` | Concrete review dimensions, such as auth boundaries, billing writes, data access, regressions, or claim accuracy. |
| `{{REVIEW_LENS — 'none', Verification-gap, Edge-case path, or a concise risk-specific lens}}` | Optional lens selected by risk after deterministic checks. |
| `{{REVIEW_SCOPE}}` | Human-readable scope name for the thing under review. |
| `{{RUN_SLUG}}` | Stable run slug for this review. |
| `{{WORKDIR}}` | Scratch run directory outside the repo under review. |

## Checks

The check validates the review contract. A passing report must have a summary and either an explicit `NO FINDINGS` verdict or one or more findings with `Finding`, `Evidence`, `Impact`, `Fix`, `Priority`, and `Confidence` labels.

This cannot be gamed by a vague review because the validator requires concrete evidence and priority/confidence fields. It also fails reports that claim the reviewer patched or committed changes.

## Review lenses

Run deterministic checks first; select a semantic lens only when the review risk calls for it.

- **Verification-gap:** ask whether changed behavior failing at a real call site would fail verification.
- **Edge-case path:** trace explicit and implicit branches, states/enums, null/empty/max boundaries, timeout/retry/partial-failure/deletion paths.

Neither lens requires a finding. Reviewers stay read-only, preserve structured evidence, and the orchestrator still dedupes and confirms reports.

## Mix with

Use `repo-feature` after synthesis when a confirmed finding needs an actual code change. Use `launch-kit` or `asset-swarm` before this when the thing under review is a launch page, media package, or public artifact produced by another swarm.

## Gotchas

Use the manifest `model` field for each task. Do not clone engine blocks or hide model choices in `engine_args`; the run state needs to show which model reviewed which artifact.

All reviewers must inspect the same surface. If one reviewer gets a different diff or broader scope, the synthesis becomes harder to trust.

Reviewers never fix. The orchestrator reads the reports, dedupes overlapping findings, confirms each against the code or artifact, and only then opens a fix task.

Use plain product issue language. Severity belongs in `Priority: P0/P1/P2/P3`; do not inflate wording beyond the evidence.

Heading regexes in checks should tolerate numbering, but the finding labels themselves should stay exact so reports can be machine-read.
