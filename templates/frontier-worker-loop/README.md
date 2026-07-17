# Frontier Worker Loop

This template is the Nate-native policy-and-template lane for a frontier orchestrator plus economical workers. The orchestrator writes the immutable contract and executable checks, freezes them, and then dispatches isolated worker lanes. Workers do not decide authority, and they do not widen scope.

## Authority Boundaries

- Frontier orchestrator: writes the contract, the executable checks, and the dispatch decision.
- Economical proven workers: implement one task in one isolated lane.
- Executable checks: decide PASS/FAIL.
- Semantic review: optional, conditional, and advisory over green results only.
- Ringside: observational.
- Paperclip: trigger/telemetry/work-product projection only.
- Beads and Vault: authoritative lifecycle and knowledge sources.

## Workflow

1. Fill the placeholders in `manifest.json` and this README.
2. Freeze the contract text.
3. Hash the exact manifest contract and place the SHA-256 in `contract_review.contract_sha256`.
4. Run the required cross-family contract review for high-risk work.
5. Lint the manifest before dispatch.
6. Run the two economical worker tasks in isolated parallel lanes.
7. Integrate only after executable checks PASS.
8. Apply semantic review only if the executable checks are green and you still need a judgment call.
9. Record the receipt and keep the routing evidence tied to the task type.

For dispatch, project the reviewed receipt into the manifest's flat
`contract_review` fields (`verdict`, reviewer provider/model/family/runtime,
`harness_attestation`, `contract_sha256`, and `artifact`). The durable standalone
receipt remains the strict nested document defined by
`schema/ringer-contract-review.v1.json`; the manifest projection exists only so
Ringer can enforce the gate and expose the evidence in run state.
`template_unfilled: true` is a human-facing marker only; the fail-closed gate is
the placeholder rejection plus exact hash, cross-family identity, PASS verdict,
and harness-attestation validation performed by Ringer.

## Review Rules

Semantic review can veto a green result if the receipts or artifact still show risk, gaps, or missing evidence. Semantic review never overrides a red executable check. Exactly one retry may receive the raw failure output from the first failure, and the retry must remain bounded to the same contract.

## Modes

| Mode | When to use | What it means |
|---|---|---|
| Canonical | Routine work with a proven route. | Use the cheapest proven worker path and rely on executable checks plus receipts. |
| Contract-reviewed high-risk | High-risk manifests or surfaces. | No dispatch until exact-manifest cross-family PASS is recorded. |
| Semantic-review | After green checks when you still need human-like judgment. | Advisory only; may veto green, cannot rescue red. |
| Bakeoff / exploration | Uncertain routing or new worker families. | Keep the mix at 80-90 percent proven / 10-20 percent exploration until roughly 20 comparable receipts justify promotion. |

## Fill / Freeze / Hash / Review / Lint / Run / Integrate / Semantic-Review / Receipt

Fill the manifest placeholders first, then freeze the contract text so it cannot drift after hashing. Hash the exact manifest contract, not a paraphrase. Review the frozen contract before dispatch when the risk tier requires it. Lint the manifest to catch structural mistakes before a worker starts. Run the worker lanes only after the contract review gate is satisfied. Integrate only after the executable checks PASS. If you need semantic-review, run it after the green executable result and treat it as a veto-only advisory layer. Finish by recording the receipt and routing future tasks from the receipt history, not from memory.

## Practical Notes

Keep the check command honest: it must print why it failed, not just that it failed. Keep the worker specs narrow enough that a cheap model can finish them reliably. Keep the template readable enough that another maintainer can fill it without reverse-engineering the lane.
