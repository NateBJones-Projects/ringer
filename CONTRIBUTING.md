# Contributing to Ringer

Honestly: we never asked for contributions, and the number of people showing up with well-built PRs has been a genuinely happy surprise. This guide exists so your work lands fast. Everything in it comes from real review decisions on real PRs, not hypothetical policy.

## The philosophy (read this first — it decides most reviews)

**Verify outputs; don't confine workers.** Ringer's entire trust model is the executed check: exit 0 is the only thing it believes. That's where correctness lives — not in guardrails around the agents. PRs that add worker restrictions, credential deny-lists, read confinement, or other agent-safety machinery will be declined as out of scope, however well built (see #15, #28). Ringer is not a security product; an operator whose threat model includes hostile workers should run them in a container or VM, and process confinement belongs upstream in the engine CLIs. No safety theater — a partial guarantee that reads as a full one makes people less safe than an honest scope statement.

**Ringer is not a model-testing harness.** Model-capability signal accrues from the eval log on real production work, not synthetic trials. Bakeoff machinery, benchmark modes, and comparison infrastructure are their own project — build them elsewhere and Ringer will happily be a component. What Ringer keeps is the scoreboard as a *byproduct of shipping*.

**Displayed data must be true.** The one unforgivable bug class here is a surface that misstates what happened — wrong model attribution, wrong lab, a dead-engine run rendering as passed. [`docs/TAXONOMY.md`](docs/TAXONOMY.md) is normative for model identity. Registry facts require sources; opinions and observations go in [`docs/MODEL-NOTES.md`](docs/MODEL-NOTES.md) as dated entries, never dressed as capability facts.

## What gets a PR merged fast

1. **Small and scoped — one feature or fix per PR.** The single biggest predictor. Four PRs merged same-day the week this guide was written; the two large bundles (52 files; 16 files) were both sent back for splitting regardless of quality. If your change has an "and," consider splitting it. Resist scope creep in your own diff: drive-by refactors, stale copies of main, and bonus features all slow the part we want.
2. **Rebased on current main.** Main moves fast here. A conflicting PR can't be audited.
3. **Executed proof for every claim.** A test that runs beats a screenshot; a check that prints *why* it fails beats a silent `exit 1`. CI runs the full suite on macOS and Linux (required) and a non-blocking `windows-latest` harness — platform claims must be proven by the job for that platform, not asserted.
4. **Match the house style.** Single-file `ringer.py`, stdlib only, Python 3.12+, frozen dataclasses, tests in `tests/` runnable by `python3 -m unittest discover -s tests`. Set `RINGER_NO_SELF_UPDATE=1` in tests that spawn the CLI.
5. **Real motivation.** PRs that fix an observed failure (say so in the description — "burned 100k tokens against a broken check" is a great opening line) review better than speculative hardening.

## Ringside UI contributions — actively encouraged

We *want* people shipping alternative Ringside faces. The rules that make a UI PR mergeable:

- **Honest data.** Build against the real `/api/runs`, `/api/library`, `/api/models` responses — capture them as test fixtures and test your rendering against the fixtures. The display contract (columns, identity taxonomy) is non-negotiable.
- **Self-contained.** No CDNs, no external fonts, no phoning out. Ringside is a local tool; vendor everything.
- **Escaped.** Run names and worker output are untrusted text.
- **Opt-in, not takeover.** The stock UI stays default; alternates arrive through an explicit selection mechanism, never by file presence. (If the selection mechanism doesn't exist yet when you read this, a small PR adding it is the ideal first piece — see #39's thread.)

## How we treat your work

- **Authorship is always preserved.** Small mechanical fixes may be pushed to your branch by a maintainer so your PR can land same-day — you remain the commit author, maintainers appear only as co-author trailers. Anything requiring judgment comes back to you as a review note instead.
- **Credit is enforced, not promised.** Every merged community contributor is listed in the README's Contributors section, and `tests/test_contributors.py` fails the whole suite if anyone with merged work is missing.
- **Declines come with reasons.** If your PR is out of scope, the note will say exactly why and what version (if any) we'd merge — and it's a direction call, not a verdict on your craft.

## Good first contributions

- A dated observation in `docs/MODEL-NOTES.md` from your own runs (what a model shines or chokes on, with the task shape).
- A template kit in `templates/` for a swarm pattern that worked for you.
- Turning the Windows CI job green (see #17's thread for the split plan).
- A capability sheet in `registry/model-capabilities/` — sourced facts only: pricing, canonical access routes, provenance, supported reasoning-effort levels, tool-calling. No marketing tiers, no routing advice, no unsourced numbers.
