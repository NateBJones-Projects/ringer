# Manifest templates

Parameterized skeletons for the five proven Ringer patterns. They encode the
practices that make swarms verifiable — checks that print why they fail,
disjoint file ownership, the worktree patch-export pattern, per-persona
session isolation — so you start from working shape instead of a blank file.

| Template | Pattern |
|---|---|
| `review-swarm.json` | N read-only scouts, one surface each, each writes a structured `report.md` |
| `fix-swarm.json` | N workers in isolated git worktrees; executed build/test check; uncommitted diff exported as a patch |
| `focus-group.json` | N personas each driving the real product through a harness; in-character reaction + out-of-character graded eval |
| `bakeoff.json` | scenarios × candidate models matrix; every cell graded against one shared contract |
| `research-with-proof.json` | cited research tasks + a proof task whose check *executes* the claim |

## How to use

1. Copy a template and replace every `{{PLACEHOLDER}}` — the placeholder text
   describes what belongs there. Placeholders make unfilled manifests obvious
   and non-functional.
2. Each template ships **one exemplar task** (two for research-with-proof).
   Duplicate the task object per surface / fix / persona / matrix cell and
   give each a unique `key`.
3. Fill `workdir` with a scratch directory outside any repo, and set
   `max_parallel` to what your machine and rate limits tolerate.
4. Sanity-check before spawning workers:

```bash
./ringer.py run manifest.json --dry-run
```

## Rules the templates assume (do not undo them)

- Checks must print WHY they fail — the retry prompt and eval log depend on
  the failure output. Keep the `|| { echo 'FAIL: ...'; exit 1; }` tails.
- In `fix-swarm`, file ownership across tasks must be disjoint — including
  against any *other* concurrent run touching the same repo — and workers
  never commit; the check exports the uncommitted diff as a patch because
  passing tasks get their worktree deleted.
- In `focus-group` and `bakeoff`, one persona/cell per task with its own
  session directory. Personas sharing a context bleed into each other.
- Keep scenario wording identical across a bakeoff row — comparability is
  the whole point.

See `.claude/skills/ringer/SKILL.md` for the judgment layer: spec-writing
craft, pattern selection, engine choice, and the post-run review ritual.
