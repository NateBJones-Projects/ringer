# Amazon Working Backwards PR/FAQ template

Use evidence-backed product prose. Replace every bracketed prompt and remove
any section that is explicitly irrelevant only when the same required truth is
covered elsewhere.

Select exactly one evidence mode. Missing or unknown mode defaults safely to
`review_handoff`; `retrospective` must be explicit and is only for an already
`MERGED` or `CLOSED` historical PR. Retain exactly one of the two outcome
sections below.

## Press Release

### Headline

[Name the customer or staff outcome, not the implementation.]

### Subheadline

[Explain who benefits, what becomes easier, and the product surface involved.]

### Dateline / Summary

**[Place] — [Evidence-backed date or “Retrospective”]** — [Summarize the
experience and state whether the change is built, merged, deployed, and
available. Do not infer launch status.]

### Review Gate Outcome: [READY or BLOCKED — review_handoff only]

[Use exactly one outcome. READY must name the one exact commit SHA covered by
all readiness evidence. BLOCKED must list every exact missing, stale, failed,
unauthorized, or mismatched prerequisite. Any later commit invalidates this
gate and requires fresh evidence.]

### Lifecycle Outcome: [RETROSPECTIVE — retrospective only]

[Use only for an explicitly selected retrospective of a MERGED or CLOSED PR.
Do not call it READY or BLOCKED and do not claim current merge readiness. Name
the actual PR state and report four separate evidence-backed facts: Built:
true/false; Merged: true/false; Deployed: true/false; Available: true/false.
Retrospective mode does not require an open-PR authorization or later-commit
invalidation statement.]

### Customer Problem

[Describe the prior customer or staff friction in plain language.]

### Customer Experience

[Walk through the new experience and its meaningful boundaries.]

### Illustrative Internal/Staff Quote

> “[Write a plausible internal perspective without presenting it as observed
> feedback.]”
>
> — Illustrative [role] perspective; not a sourced customer quote

Replace this only with a traceable sourced quote and identify the source.

### Getting Started

[Explain what the user does next. Gate instructions on confirmed deployment or
availability when those states are not established.]

## Customer FAQ

### What changed for me?

[Answer from the customer or staff point of view.]

### What stays the same?

[State preserved behavior and boundaries.]

### Is this available now?

[Distinguish built, merged, deployed, and confirmed available.]

## Internal FAQ

### What is the exact scope, and what are the non-goals?

[List included issues and behaviors, then explicit exclusions.]

### What verification evidence supports this narrative?

[List executed worker checks, full repository gates, independent review, user
experience checks, and required-check results exactly. In `review_handoff`, for
each item name the commit SHA it covers; state the local HEAD, pushed remote
branch head, and GitHub PR headRefOid and whether all three are the same SHA;
never accept UNKNOWN mergeability. In `retrospective`, identify evidence as
historical and name its actual SHA and age when known without implying current
readiness.]

### What authorization boundary applies?

[Push, PR creation/update, PR body/comment writes, merge, and deployment are
external actions requiring existing user authorization. In `review_handoff`,
state which authorized pushed branch and open PR already existed for
inspection; if either is absent or unauthorized, this package is BLOCKED. In
`retrospective`, do not require historical open-PR authorization, but keep the
authorization boundary for any new external action.]

### What is the safety, data, and migration impact?

[State production-data, credential, infrastructure, and migration effects.]

### What is true about rollout, deployment, and production availability?

[State Built, Merged, Deployed, and Available separately and identify evidence
or evidence gaps for each. A retrospective must use explicit true/false values
for all four and name whether the historical PR state is MERGED or CLOSED.]

### What rollback and observability evidence exists?

[Name verified mechanisms or say that none were supplied; do not invent them.]

### What is the next human decision?

[Name the exact blocker-resolution, merge, deploy, release, or follow-up
decision appropriate to the selected mode. Do not present a retrospective as a
current merge decision. Stop before merge or deployment and before any other
unauthorized external action.]
