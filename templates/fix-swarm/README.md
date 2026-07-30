# fix-swarm

## What it is

A fix swarm applies independent confirmed fixes in isolated git worktrees. Each worker owns a disjoint file list, leaves changes uncommitted, and lets the check export a patch outside the throwaway worktree.

Use it after the orchestrator has already decided what should be fixed. It is a repair tool, not a discovery tool.

## When to use

- Applying several unrelated bug fixes in parallel.
- Mechanical but verified edits across separate files or modules.
- Turning confirmed review findings into patch files for human/orchestrator review.
- Running the same build or test command per fix before integrating anything.

## Fill in

| Placeholder | What goes there |
|---|---|
| `{{PROJECT}}` | Short project or repo name for the run name and worker brief. |
| `{{WORKDIR}}` | Absolute scratch directory outside the repo; exported patches and summaries land here. |
| `{{REPO_PATH}}` | Absolute path to the repo Ringer will clone into per-task worktrees. |
| `{{FIX_KEY}}` | Stable task key for one fix, used in the exported patch filename. |
| `{{OWNED_FILES — every file or directory this task may modify, comma or newline separated}}` | Exact file or directory ownership list. It must be disjoint from every other worker and concurrent run. |
| `{{FINDING — the confirmed bug or issue, with file:line evidence and the desired behavior}}` | The specific issue to fix, including evidence and what correct behavior looks like. |
| `{{LOCAL_VERIFY — exact command or manual check to understand the failure}}` | Reproduction or inspection step the worker should run before editing. |
| `{{BUILD_OR_TEST_COMMAND — exact command that proves the fix and prints useful errors}}` | Command the validator executes after the worker edits. It should fail loudly when the fix is wrong. |
| `{{CHECK_SCRIPT_PATH — absolute path to templates/fix-swarm/checks/fix-swarm.py}}` | Absolute path to this kit's validator after you copy or reference the kit. |

## Checks

The manifest invokes `checks/fix-swarm.py`. The validator runs the filled build/test command, validates `fix-summary.md`, stages the worktree, rejects patches that touch files outside the ownership list, and writes a non-empty patch plus summary into the run workdir. For compatibility it updates the canonical `<key>.patch` path on each invocation. It also writes a sibling attempt archive, such as `<key>.attempt1.patch` or `<key>.attempt2.patch`, so retries do not destroy earlier patch output.

`expect_files` is intentionally empty because worktrees mode deletes passing task worktrees. The durable deliverables are check-produced files at the filled patch and summary paths in `WORKDIR`; review those before applying anything. When a task is retried, inspect the attempt-specific patch archives if you need to compare attempts.

## Mix with

- `review-swarm`: run review first, confirm the findings yourself, then feed only confirmed issues into fix-swarm. Never use the same worker to find and fix.
- `research-with-proof`: use it when the correct fix depends on a current API behavior, standard, or executable claim.
- `bakeoff`: use a small fix task as a model comparison only when the same owned files and same check can be used for every candidate.

## Gotchas

- Worktrees that pass are deleted. Anything not exported by the check is gone.
- The canonical `<key>.patch` is always the latest validator output. Earlier outputs are preserved as `<key>.attemptN.patch` siblings, with the next number chosen from existing attempt archives.
- `git add -A` cannot stage ignored build outputs. If a fix must preserve ignored files, extend the validator to copy them outside the worktree.
- The ownership list is the safety rail. If a fix needs more files, stop and make a new task boundary.
- Workers do not commit. The orchestrator applies patches after review.
