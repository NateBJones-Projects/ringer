---
name: amazon-pr-faq
description: >-
  Create evidence-backed Amazon Working Backwards press release/FAQ documents
  and review packages. Use for an Amazon-style Working Backwards press release
  and FAQ, a PR FAQ, a product release narrative, or the end-of-run review
  package after a clean Ringer delivery.
---

# Amazon PR/FAQ

Build a customer-led release narrative from verified delivery evidence. Keep
implementation detail in the Internal FAQ and stop at the human merge or deploy
decision.

## Choose the package mode

Every evidence packet has one of two modes:

- `review_handoff` is the safe default. Use it for a current PR-facing
  handoff. A missing or unknown `mode` value is treated as `review_handoff`;
  it must never weaken the readiness gate.
- `retrospective` must be selected explicitly with `"mode": "retrospective"`
  and is only for an already `MERGED` or `CLOSED` historical PR. It reports a
  lifecycle record, not current merge readiness.

The Ringer post-run hook always invokes `review_handoff`. Do not switch that
hook to `retrospective` to bypass a missing open PR, authorization, same-SHA
evidence, review, mergeability, or required check.

## Gather evidence

1. Inspect the exact pull request, diff, linked issues, executed check results,
   independent review, and GitHub state relevant to the selected mode. Prefer
   live evidence; label historical, stale, or missing evidence explicitly.
2. In `review_handoff`, record the local `HEAD`, pushed remote branch head, and
   GitHub PR `headRefOid`. Worker gates, full-repository gates, independent
   review, and required checks must each name the exact commit SHA they cover.
3. Record what was built, merged, deployed, and confirmed available as four
   separate facts. Do not infer one state from another.
4. Record scope, non-goals, data or migration effects, rollout evidence,
   rollback or observability evidence, and the next human decision.
5. Do not invent customer quotes, metrics, adoption, time savings, launch dates,
   or production status. Label an unsourced internal/staff quote as
   illustrative.

## `review_handoff`: enforce the review gate

The package must state exactly one explicit outcome: `Review Gate Outcome:
READY` or `Review Gate Outcome: BLOCKED`.

Call the package ready only when all of these are true:

- An existing authorized pushed branch and existing authorized open,
  non-draft PR are available for inspection. Do not push or open/update a PR to
  manufacture these prerequisites.
- Local `HEAD`, the pushed remote branch head, and GitHub `headRefOid` are the
  same SHA.
- Executed worker and full-repository gates pass for that exact SHA.
- A fresh independent review of that exact SHA has no actionable findings.
- GitHub reports the PR as mergeable, never `UNKNOWN`, and every required check
  is successful for that exact SHA.

If any condition is absent, draft only a blocked review package, copy every
exact blocker into it, report it to the human, and stop. Any later commit
invalidates the entire gate; collect fresh checks, review, branch heads, and PR
state for the new SHA.

Push, PR creation/update, PR body or comment writes, merge, and deployment are
external actions requiring existing user authorization. Never perform one just
to satisfy or publish this package. An unauthorized or absent pushed branch or
open PR is a blocker. Always stop before merge or deployment.

Use this readiness shape in the evidence JSON: `commit.local_head` and
`commit.pushed_remote_branch_head`; `pr.headRefOid`, `pr.state`, `pr.isDraft`,
and `pr.mergeable`; `authorization.pushed_branch` and
`authorization.open_pr`; and `verification.worker_gates`,
`verification.full_repository_gates`, `verification.independent_review`, and
`verification.required_checks`. Every gate, review, and required-check record
must contain `name`, `conclusion`, and `sha`; the independent review must also
contain `fresh: true`.

## `retrospective`: report lifecycle truth

Use this mode only when `pr.state` is `MERGED` or `CLOSED`. State exactly one
outcome, `Lifecycle Outcome: RETROSPECTIVE`, and do not label the package READY
or BLOCKED, claim that the historical PR is currently merge-ready, or imply
that it passed today's review gate.

The evidence JSON must include explicit booleans for `lifecycle.built`,
`lifecycle.merged`, `lifecycle.deployed`, and `lifecycle.available`. The
document must state all four separately, name the actual `MERGED` or `CLOSED`
PR state, and identify the evidence or evidence gap for each. A `MERGED` PR
requires `lifecycle.merged: true`; a `CLOSED` PR requires
`lifecycle.merged: false`.

Retrospective mode does not require an authorized open PR, matching current
branch heads, current mergeability, or a later-commit invalidation statement.
Historical checks and reviews may be included when available, but must retain
their actual SHA and age. External writes, follow-up changes, deployment, and
release actions still require user authorization. Never infer deployed from
merged or available from deployed.

## Write and validate

1. Read [references/amazon-pr-faq-template.md](references/amazon-pr-faq-template.md)
   and preserve its substantive sections.
2. Lead with the customer or staff problem and experience. Put code, files,
   checks, safety, rollout, and rollback detail in the Internal FAQ.
3. Include the PR number and URL when supplied and required issue references.
   In `review_handoff`, include the explicit READY/BLOCKED outcome, exact gate
   SHA, and verification or blocker facts. In `retrospective`, include the
   RETROSPECTIVE lifecycle outcome and the four explicit lifecycle facts.
4. Validate the draft:

   ```bash
   python3 scripts/validate_pr_faq.py FAQ_PATH --evidence EVIDENCE_JSON
   ```

5. Present the PR URL and FAQ to the human. Add the document to a PR body,
   comment, or external system only under existing user authorization. Stop
   before merge or deployment.
