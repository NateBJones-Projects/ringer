# review-swarm

## What it is

A read-only review swarm splits a large review across multiple scouts. Each worker owns one surface, writes one structured report, and never edits the repo.

Use it when one context window cannot review the whole system well. The orchestrator reads the reports, confirms the findings, and decides what becomes fix work.

## When to use

- Pre-merge review across routes, modules, jobs, migrations, prompts, or docs.
- Finding likely bugs before a repair pass.
- Auditing a risky surface where evidence and line references matter.
- Comparing review quality across engines without giving any worker write access.

## Fill in

| Placeholder | What goes there |
|---|---|
| `{{PROJECT}}` | Short project or repo name for the run name and worker brief. |
| `{{WORKDIR}}` | Absolute scratch directory where Ringer creates task directories. |
| `{{SURFACE_KEY}}` | Stable task key for one reviewed surface, such as `auth-routes` or `billing-ui`. |
| `{{ONE_LINE_DESCRIPTION}}` | One sentence describing the product or repo being reviewed. |
| `{{REPO_PATH}}` | Absolute path to the repo or source bundle the scout may read. |
| `{{SURFACE_DESCRIPTION — the page, module, route, workflow, or subsystem this scout owns, with entry-point file paths}}` | The exact review boundary and entry files for this scout. |
| `{{REVIEW_DIMENSIONS — e.g. correctness, auth, data access, billing or credit integrity, copy, dead code, migration risk}}` | The categories the scout must explicitly judge. |
| `{{REVIEW_LENS — 'none', Verification-gap, Edge-case path, or a concise risk-specific lens}}` | Optional lens selected by risk after deterministic checks. |
| `{{LOCAL_VERIFY — exact read-only command, or 'none'}}` | A command that proves a read-only fact, or `none` when inspection is enough. |
| `{{CHECK_SCRIPT_PATH — absolute path to templates/review-swarm/checks/review-swarm.py}}` | Absolute path to this kit's validator after you copy or reference the kit. |

## Checks

The manifest invokes `checks/review-swarm.py` against `report.md`. The validator requires the named sections, enforces the summary length cap, and checks that every finding has Evidence, Impact, Fix, Priority, and Confidence fields.

This is harder to game than a file-exists check because a lazy report must still satisfy the review contract. It does not prove the finding is correct; the orchestrator still confirms evidence before turning a report into repair work.

## Review lenses

Run deterministic checks first, then select a lens only where risk warrants it.

- **Verification-gap:** ask whether changed behavior failing at a real call site would fail verification.
- **Edge-case path:** trace explicit and implicit branches, states/enums, null/empty/max boundaries, timeout/retry/partial-failure/deletion paths.

Lenses guide evidence gathering; they do not require findings. A clean report remains valid, and the orchestrator confirms every reported finding.

## Mix with

- `fix-swarm`: review-swarm feeds fix-swarm. Use scouts to find and explain issues, then give confirmed findings to separate fix workers. Do not let the same worker find and fix.
- `research-with-proof`: use it when a review finding depends on a current external fact, API behavior, or executable claim.
- `bakeoff`: run the same review surface through multiple candidate models when you need evidence about review quality.

## Gotchas

- Keep surfaces disjoint. Two scouts reviewing the same broad area produce duplicate noise.
- A report without file:line evidence is not a finding; it belongs under Assumptions.
- Do not add write commands to this kit. Read-only is the point.
- If a dimension is clean, the worker must say so. Silence is not a verdict.
