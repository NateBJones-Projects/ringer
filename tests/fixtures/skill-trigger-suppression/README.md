# Skill-trigger suppression fixtures

These are two worker specs modelled on a real failure. The originals came from a
private project, so identifiers, paths, hashes and the provider CLI have been
replaced; the structure, the trigger-relevant phrasing and the retry wrapper are
kept as they were.

What happened: two workers, each a Claude Code session in an isolated directory
under `~/.ringer/work/`, were told to make exactly one paid image generation call
and save evidence. Both instead loaded the user-level `ringer` skill (the
orchestrator playbook), reframed their own spec as a suspicious worker transcript,
and spent the session on diagnostic exploration. Neither made the call.

Note what the specs actually contain: each is a RETRY prompt. After the isolation
notice, role, budget, hashes and steps, the prompt embeds the previous attempt's
result (the first attempt had stopped because `shasum` was not permitted in the
worker sandbox, so it could not verify the hashes). That embedded result is what
the skill's trigger phrase "reviewing or diagnosing failed worker output" matched.
The off-switch therefore names retry prompts explicitly. The underlying sandbox
gap (no hashing tool permitted for a worker asked to verify hashes) is a separate
defect in the task's `--allowedTools` list, not in the skill.

Expected behaviour for a worker given either spec: the `ringer` skill is NOT
loaded, no orchestrator framing appears, the worker verifies the hashes and
either makes the single call or STOPs per the spec.

How to use: when editing the skill's trigger text (`.claude/skills/ringer/SKILL.md`
front matter and "Rule zero"), re-read both specs and confirm every clause of the
off-switch still matches them: ISOLATION NOTICE, "Ignore any inherited
instructions", "exactly ONE paid ... call", "do not invoke any other worker",
working directory under `~/.ringer/work/`, and an embedded previous-attempt
result. There is no automated test; the trigger is prose read by a model, so this
is a review fixture, not a unit test. The paths inside the specs do not need to
resolve.
