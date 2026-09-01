You are the fresh, read-only semantic Judge for JAC-3643. Do not edit files,
commit, push, dispatch workers, or create artifacts. Review the immutable Git
range 874ac17370e84a2fd613651fbb78b4861233c7d1..71dd9ca against:

- contracts/jac-3643-nate-native-upgrade.v1.json
- manifests/jac-3643-nate-native-upgrade.ringer.json
- Nate-native Ringer purpose: frontier orchestrator specifies and freezes the
  contract/checks; economical proven workers implement; executable checks are
  authoritative; exactly one retry gets raw failure output; receipts drive
  per-task-type routing; semantic review is conditional and veto-only.
- Authority boundaries: Paperclip is trigger/telemetry/work-product projection;
  Ringside is observational; Beads/Vault remain authoritative.

Inspect the full diff and relevant surrounding code/tests. Specifically verify:

1. Old manifests remain compatible and `risk`, `orchestrator`,
   `contract_review`, and canonical `contract_sha256` propagate through live and
   final state plus attempt evidence.
2. High/critical runs are actually blocked before dry-run or worker dispatch
   unless the exact hash has a cross-family PASS with harness-attested reviewer
   identity. A semantic PASS cannot override red executable checks.
3. The reusable template matches the core parser, is non-dispatchable while
   unfilled, and its checks truly pass and fail rather than merely looking
   plausible.
4. Retry behavior stays exactly two attempts total and raw failure output still
   reaches the one retry unchanged.
5. No authority inversion, security regression, silent compatibility break, or
   fabricated evidence was introduced.

The orchestrator reports these already-executed checks; treat them as evidence
to audit, not as claims you may rewrite:

- focused: 49 passed, 21 subtests passed
- full: 172 passed, 21 subtests; exactly two pre-existing baseline failures
  (missing external design-reference file; date-sensitive scoreboard assertion)
- full excluding those two baseline defects: 170 passed, 1 deselected,
  21 subtests passed
- Ringer run jac-3643-nate-native-ringer-upgrade-20260717T030002Z-p84316:
  both gpt-5.4-mini worker lanes PASS on attempt 1, 283,817 total tokens

Return the required structured verdict. PASS means no requirement gap or
required change remains. Put non-blocking improvements only in advisories.
