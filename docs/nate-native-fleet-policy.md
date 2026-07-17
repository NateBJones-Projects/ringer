# Nate-Native Fleet Policy

## Control Loop

The frontier orchestrator writes the immutable contract and executable checks, then freezes both before any worker dispatch. Economical proven workers implement in isolated parallel lanes and own only their task outputs. Executable checks decide PASS/FAIL. Exactly one retry receives raw failure output from the first failure, and no task gets open-ended retries. Receipts drive per-task-type routing so the next worker choice is evidence-based instead of speculative. Semantic review is conditional and can veto green but never override red.

## Authority Boundaries

Ringside is observational. Paperclip is trigger/telemetry/work-product projection. Beads and Vault remain authoritative. This lane may describe policy, templates, checks, and receipts, but it does not become a second queue, a second lifecycle ledger, or a second source of truth.

## Operating Modes

### Canonical

Use canonical mode for routine work with a proven worker class and no extra review gate beyond executable checks and receipt capture. The orchestrator still writes the immutable contract and checks before dispatch.

### Contract-Reviewed High-Risk

Use this when the manifest, surface, or change is high risk. A high-risk run is not dispatchable before exact-manifest cross-family PASS. The contract review receipt must be harness-attested, and a semantic opinion cannot substitute for the executable gate.

### Semantic-Review

Use semantic-review mode only after the executable checks are green. The reviewer may veto a green result if the receipt or artifact still has unresolved gaps, but the reviewer can never turn a red executable check into PASS.

### Bakeoff / Exploration

Use bakeoff/exploration to compare candidates, prompt shapes, or worker families. The default mix is 80-90 percent proven / 10-20 percent exploration. Promote an exploratory route only after roughly 20 comparable receipts, not after one lucky run.

## Routing Policy

Routing should prefer the cheapest worker that has already demonstrated fitness for the task type and contract shape. Compare receipts by task_type, model family, runtime, and the exact executable checks that were run. When the receipt history is sparse, keep the task in exploration instead of pretending the model is proven.

## Non-Negotiables

- The frontier orchestrator writes the immutable contract and executable checks.
- economical proven workers implement in isolated parallel lanes.
- executable checks decide PASS/FAIL.
- Exactly one retry receives raw failure output.
- Receipts drive per-task-type routing.
- semantic review is conditional and can veto green but never override red.
- Ringside is observational.
- Paperclip is trigger/telemetry/work-product projection.
- Beads/Vault remain authoritative.
