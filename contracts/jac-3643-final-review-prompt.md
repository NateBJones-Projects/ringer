You are a fresh, read-only final semantic Judge for JAC-3643. Do not edit,
write, commit, push, install, restart, dispatch workers, or call mutating APIs.

Review the full immutable range
874ac17370e84a2fd613651fbb78b4861233c7d1..b79b4a2 and the frozen contract at
contracts/jac-3643-nate-native-upgrade.v1.json. Inspect the exact full commit SHA
from Git before issuing a verdict.

The previous fresh Opus review of 71dd9ca returned PASS with no gaps or required
changes, but advised adding a defense-in-depth gate inside RingerRunner because
a future programmatic caller could bypass the CLI main() gate. The new delta is
intended to close that advisory, add a direct test, document that
template_unfilled is cosmetic, and preserve the first review receipt. Verify the
delta actually does so without breaking valid high-risk construction or old
low-risk manifests.

Also recheck the full requirements: frontier-orchestrated immutable contract and
checks; economical workers; executable PASS/FAIL; exactly one retry with raw
failure output; receipts by task_type; conditional veto-only semantic review;
Paperclip projection, Ringside observation, and Beads/Vault authority. Confirm
high/critical dispatch fails closed for both CLI and direct RingerRunner paths
unless exact-hash, cross-family, harness-attested PASS review evidence exists.

Executable evidence already run after the delta:

- focused: 50 passed, 21 subtests passed
- all tests excluding the two known baseline defects: 171 passed,
  1 deselected, 21 subtests passed
- full: 173 passed, 21 subtests, with exactly the same two baseline failures
  (missing external design-reference file; date-sensitive scoreboard assertion)
- contracts/jac-3643-post-integration-review-opus.v1.json validates against
  schema/ringer-contract-review.v1.json

Return the required structured verdict. PASS means no requirement gap or
required change remains. Non-blocking improvements belong only in advisories.
