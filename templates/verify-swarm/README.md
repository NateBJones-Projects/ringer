Blueprint — adapt with care

# Verify Swarm

## What it is

A hostile-verification round that runs AFTER any finder swarm (adversarial-review, review-swarm): each finding goes to N cheap refuters whose only job is to disprove it by direct inspection of the sources. The orchestrator adjudicates only the survivors and the splits, so the expensive model reads verdicts instead of re-deriving every finding from scratch.

The premise: a well-formed finding is not a true finding. Format checks catch lazy reports; only re-inspection catches fabricated ones. Minions do not trust minions.

## When to use

Use it between a finder swarm and the orchestrator's synthesis whenever findings will drive real work (doc changes, fix swarms, merge decisions). Use it especially when a finder model is new or has a fabrication history — the verify round is what makes a cheap, unproven finder usable at all.

Skip it when the orchestrator is going to read every cited line anyway (tiny finding sets), or when findings are matters of judgment rather than checkable claims (persona feedback, style review).

## Fill in

| Placeholder | What goes there |
|---|---|
| `{{RUN_SLUG}}` | SAME run_name as the finder round — one job, one artifact, rounds accumulate. |
| `{{WORKDIR}}` | Scratch run directory for this round, outside any repo under review. |
| `{{FINDING_ID}}` | Stable id for the finding (f1, f2, ...). |
| `{{FINDING_TEXT}}` | The finding VERBATIM as its author claimed it — citations, priorities, and all. Refuters test the claim, not your paraphrase. |
| `{{REFUTER_MODEL}}` | Model slug for this refuter. NEVER the model that produced the finding. |
| `{{SUBJECT_PATHS}}` | Read-only paths (doc, repo) every refuter inspects. |
| `{{KIT_DIR}}` | Absolute path to `templates/verify-swarm` after copying or installing this kit. |

## Checks

`checks/check_verdict.py` requires a Verdict line that says exactly CONFIRMED or REFUTED (and not both), an Evidence section with at least one concrete file citation, and a Reasoning section. It fails reports that claim the verifier edited anything. It prints which requirement broke.

The check validates the verdict CONTRACT; it cannot validate the verdict's truth. That is the orchestrator's adjudication seat, now narrowed to survivors and splits.

## Adjudication rule (orchestrator side)

- All refuters REFUTE -> finding dies; record why in the synthesis.
- All refuters CONFIRM -> finding survives; orchestrator spot-checks at least one.
- Split -> orchestrator reads the disagreement and decides; splits are the round's yield, spend your tokens there.

## Canary rule

When proving a NEW refuter panel, seed one known-false finding (a real fabrication from a past run beats an invented one) without marking it. A verify round that confirms the canary is a broken round: tighten the refuter spec or swap models before trusting any of its verdicts. A verify swarm that cannot kill a known-false finding is trusting the finders with extra steps.

## Mix with

Run after `adversarial-review` or `review-swarm`, before synthesis. Survivor findings feed `fix-swarm` or doc updates. Verify slots are good exploration lanes: refutation of one finding is small, checkable work — audition untested cheap models here.

## Gotchas

Never let a model refute its own finding; with co-authored (deduped) findings, exclude every co-author.

Give refuters the finding verbatim. A paraphrase that fixes the author's overclaim makes the canary unkillable and the round worthless.

Two refuters is the efficient default: unanimous kill, unanimous survive, or a split for the orchestrator. Three only when a finding gates something irreversible.

Refuters inherit the finder round's read-only mounts. If the finder could read it, the refuter must be able to read it, or "could not locate evidence" produces false REFUTED verdicts.

Pin the subject to a commit. A verify round judges truth-NOW, not truth-at-finding: if the code or doc moves between rounds (including fixes prompted by the findings themselves, or the findings being folded into the doc), refuters correctly refute findings that were true when found, and REFUTED stops meaning "fabricated" (2026-07-12 gx lesson: 5 commits landed between rounds; most REFUTED verdicts decoded as "already fixed", and one finding was refuted by citing the finder round's own addendum). Stamp the finder round's HEAD SHA into every finding and tell refuters to either judge at that SHA (worktree mount) or report "refuted because fixed since <sha>" as its own verdict category.
