# Split Decision

## What it is

One decision question, fanned out to several workers on **different providers**, each writing one independent position in an isolated task directory. The check validates each position's contract — metadata, an explicit stance, a one-sentence summary, substantive reasoning — mechanically. What the swarm produces is the raw material for a decision *record*: positions in, one record out, a human decides.

Ringer is a natural fit for this pattern because per-task `engine` routing and isolated task directories give you provider independence by construction rather than by convention, and the run's JSONL log (`worker_engine`, `duration_ms`, `worker_tokens` per attempt) is linkable evidence that the positions were generated in parallel and independently.

This kit is the sweep phase of the [AIDR](https://github.com/snapsynapse/aidr) "Split Decision" recipe (see the worked example in that repo's `RECIPES.md`, which is this exact manifest shape). You don't need AIDR to use the kit — three validated independent positions are useful input to any decision process — but if you want a lintable one-file decision record out the other end, the assembly phase below produces one.

## When to use

Use this before committing to a consequential, contested decision: an architecture choice, a scope cut, a ship/hold call, a naming or licensing question. The failure mode it prevents is the echo chamber — one model's framing anchoring every subsequent opinion. It is not a code-review pattern (use `adversarial-review` for reviewing an artifact) and not a bakeoff (positions argue a decision; they don't compete on a benchmark).

## Fill in

| Placeholder | What goes there |
|---|---|
| `{{AGENT_A}}` / `{{AGENT_B}}` / `{{AGENT_C}}` | Human-readable label for each panelist, shown in the position block (e.g. a role like `infrastructure-strategist`, or just the model's name). |
| `{{ALTERNATIVES}}` | The alternatives already on the table, stated neutrally — don't pre-rank them. |
| `{{CONSTRAINTS}}` | Hard constraints a position must respect: budget, license, deadline, maintainer bandwidth. |
| `{{DECISION_CONTEXT}}` | Short background: the project, what led to this question, what evidence exists. |
| `{{DECISION_QUESTION}}` | The single question to decide, phrased so a stance is possible. |
| `{{ENGINE_A}}` / `{{ENGINE_B}}` / `{{ENGINE_C}}` | Engine per panelist. Independence comes from **different providers**, so prefer three engines that resolve to three different labs. |
| `{{KIT_DIR}}` | Absolute path to `templates/split-decision` after copying or installing this kit. |
| `{{MODEL_A}}` / `{{MODEL_B}}` / `{{MODEL_C}}` | Model slug where the engine takes one (e.g. `opencode` lanes); empty uses the engine default. |
| `{{RUN_SLUG}}` | Stable run slug for this decision. |
| `{{WORKDIR}}` | Scratch run directory. |

Every panelist receives the identical brief. If you add panelists, duplicate a task and keep the spec byte-identical apart from the agent label.

## Checks

`check_position.py` validates the position contract and prints exactly what's missing: the `### Position:` heading, the five metadata lines (`agent`, `model`, `provider`, `stance`, `summary`), a stance from the closed vocabulary (`recommend | oppose | alternative | abstain`), a real one-sentence summary, and a floor on reasoning length. It fails a file containing more than one position block — the usual symptom of another participant's output leaking in — and it fails an `abstain` that doesn't say what information is missing.

What the check cannot prove: that two engines resolve to genuinely different models (the position's self-reported `model`/`provider` lines are a declaration; the run log and Ringer's model-identity registry help corroborate), and that the reasoning is any good. Reading the positions is the orchestrator's job; deciding is a human's.

## Assembling a record (second phase, optional)

Ringer tasks have no ordering, so assembly cannot be a task that waits on the others. After the sweep exits, copy each task directory's `position.md` up to flat files, then assemble and lint with the AIDR tools (Apache-2.0, separate repo):

```bash
mkdir -p positions
cp <workdir>/position-a/position.md positions/a.md   # repeat per task
node tools/aidr-assemble.mjs --id AIDR-NNNN --title "..." --brief brief.md \
  --positions positions/ --out decisions/
node tools/aidr-lint.mjs decisions/AIDR-NNNN-*.md | grep 'PASS'
```

Or make the second phase a one-task Ringer manifest whose `check` is exactly that lint gate — exit code zero is then the swarm's own evidence that two or more distinct providers recorded positions before any arbitration existed.

## Mix with

Use `probe` first if any engine in the panel is new to your machine — prove the lane before trusting it with a panelist seat. Use `adversarial-review` afterwards if the decision produces an artifact worth reviewing. The arbitration itself never mixes with anything: it's a human reading positions, not a task.

## Gotchas

Independence is the entire product. The isolation is mechanical (separate task directories) *and* behavioral (the spec's never-read-other-output rule); both travel together, and the spec must stay byte-identical across panelists so no one gets a different brief.

The independence axis is the **provider**, not the temperature. Three tasks on one provider's model produce correlated positions with different wording; prefer three engines resolving to three different labs, and use the `model` field — never cloned engine blocks — where a lane needs a specific model.

Don't pre-rank the alternatives in the brief. A brief that says "we're leaning toward X" collapses the panel into confirmation.

Positions argue from the brief alone. If a position needs evidence the brief doesn't contain, that's a brief problem — fix the brief and re-run the panel, don't let panelists invent citations.
