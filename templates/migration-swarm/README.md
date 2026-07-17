# Blueprint — adapt with care

# migration-swarm

## What it is

A migration swarm is for one mechanical transform applied across many disjoint file sets. Each worker edits only its owned files in an isolated git worktree, leaves changes uncommitted, and the check exports the patch before Ringer deletes a passing worktree.

This is a blueprint, not a recorded proven kit. Use it when the transform is simple enough to specify with before/after examples and strict exclusions.

## When to use

Use this for API renames, framework upgrade steps, import-path rewrites, codemod cleanup, and pattern replacements where workers can own non-overlapping files.

Do not use this for judgment-heavy refactors, cross-file architecture changes, or migrations where one worker needs to edit files another worker also owns. Run a review-swarm first if the task list is not already clean.

## Fill in

| Placeholder | What goes there |
|---|---|
| `{{PROJECT}}` | Short project name used in the run name. |
| `{{WORKDIR}}` | Scratch run directory outside the repo. |
| `{{REPO_PATH}}` | Absolute path to the repo that Ringer should create task worktrees from. |
| `{{MIGRATION_KEY}}` | Stable task key; also becomes the exported patch filename. |
| `{{OWNED_FILES — semicolon-separated repo-relative files or directory prefixes this worker may modify}}` | The exact repo-relative files or directory prefixes this worker may change, separated with semicolons. Directory prefixes should end with `/`. |
| `{{TRANSFORM_RULE — exact before/after rule, API rename, framework upgrade step, exclusions, and one concrete example}}` | The mechanical rule, including before/after examples and exclusions. |
| `{{LOCAL_VERIFY — exact command from this worktree that gives useful output, e.g. npm test -- --runInBand path/to/test}}` | The command the worker should run before finishing. |
| `{{EXPORT_DIR}}` | Absolute directory outside all task worktrees where checks write `<task-key>.patch` and ignored-file copies. Create it before the run. |
| `{{GITIGNORED_EXPORTS}}` | `NONE` for normal tracked-file migrations, `AUTO` to copy every ignored path found under the owned paths, or a semicolon-separated list of ignored owned paths to copy. |
| `{{PYTHON}}` | Python executable for the check script, for example `python3`. |
| `{{KIT_DIR}}` | Absolute path to `templates/migration-swarm` in this Ringer checkout or copied kit location. |

## Checks

The check runs `git add -A && git diff --cached > {{EXPORT_DIR}}/{{MIGRATION_KEY}}.patch`, then runs `checks/migration_patch_check.py`.

The script fails loudly if the patch is empty, if any staged file is outside the task's owned set, or if ignored files under the owned paths would be lost. When `{{GITIGNORED_EXPORTS}}` is `AUTO` or a path list, the script copies those ignored files into `{{EXPORT_DIR}}/{{MIGRATION_KEY}}-gitignored/` and verifies the copies exist.

This cannot be gamed by a worker saying "done" because the deliverable is the staged git diff, not the worker summary. It does not prove semantic correctness; the orchestrator still applies exported patches serially and runs the full build after each patch.

`expect_files` is empty on purpose. In worktrees mode, the patch is produced by the check outside the task worktree; Ringer's normal task-file harvest would otherwise point at files that are deleted after PASS.

## Mix with

Use `templates/review-swarm.json` first when the file partition or transform rule needs discovery. Use `templates/fix-swarm.json` for a small set of independent bug fixes where each task has its own finding instead of one shared mechanical rule.

Use `templates/test-hardening/` after the migration to add coverage around changed modules, especially before applying many patches to the real branch.

## Gotchas

Passing tasks get their worktree deleted. The patch is the deliverable.

Workers must leave changes uncommitted. A worker commit dies with the deleted worktree.

The orchestrator applies patches serially and runs the full build after each patch. Do not batch-apply every patch and hope the final build explains which task broke the branch.

Gitignored edits are not included in `git diff --cached`. If a task touches ignored outputs, set `{{GITIGNORED_EXPORTS}}` to `AUTO` or an explicit path list so the check copies them out.

