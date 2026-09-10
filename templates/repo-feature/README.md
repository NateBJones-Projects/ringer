# Repo Feature

## What it is

A one-worker or few-worker pattern for editing a real repository while keeping the worker sandboxed to explicit writable roots. The check runs the repo's real build or tests, asserts required content, and rejects git status changes outside owned paths.

This is the right shape when the deliverable is a code change in an existing app, not a standalone artifact in the task directory.

## When to use

Use this for narrowly scoped app pages, route additions, component changes, scripts, docs generated inside a repo, or one small feature with clear ownership. Keep parallelism low unless each worker owns disjoint files and the checks cannot collide.

## Fill in

| Placeholder | What goes there |
|---|---|
| `{{ALLOWED_STATUS_PATHS_CSV}}` | Additional pre-existing or explicitly allowed git-status paths, comma-separated. |
| `{{BUILD_OR_TEST_COMMAND}}` | Real repo verification command, such as `npm run build` or `pytest`. |
| `{{CONVENTION_FILES}}` | Read-only files the worker should inspect before editing. |
| `{{ENGINE_BUILD}}` | Engine name for the repo-edit worker. |
| `{{FEATURE_BRIEF}}` | The concrete feature request and acceptance criteria. |
| `{{HOW_TO_RUN_COMMANDS}}` | Exact commands the worker should run before finishing. |
| `{{KIT_DIR}}` | Absolute path to `templates/repo-feature` after copying or installing this kit. |
| `{{OWNED_FILES_CSV}}` | Comma-separated repo paths the worker may modify. |
| `{{PROJECT_NAME}}` | Human-readable app or repo name. |
| `{{REPO_PATH}}` | Absolute path to the writable repository checkout. |
| `{{REQUIRED_PATHS_CSV}}` | Repo paths that must exist after the task, comma-separated. |
| `{{REQUIRED_TEXT_CSV}}` | Text snippets that must appear somewhere in owned files, comma-separated. |
| `{{RUN_SLUG}}` | Stable run slug for this repo feature. |
| `{{TASK_KEY}}` | Unique task key, such as `demo-page` or `settings-route`. |
| `{{WORKDIR}}` | Scratch Ringer workdir outside the repository. |

## Checks

The check verifies four things: `notes.md` exists in the scratch task directory, required repo paths exist, required text appears in owned files, the configured build/test command passes, and `git status --porcelain` contains only owned or allowlisted paths.

This cannot be gamed by creating a loose artifact in the task directory because the real repo command executes in `{{REPO_PATH}}` and the git porcelain check catches unrelated edits.

### Check budget

Because the check runs a real build or test suite, it declares `"check_timeout_s": 1800` rather than relying on the 60-second default. A check is killed when it exceeds its budget, and a killed check reports `TIMEOUT` — indistinguishable, on the scoreboard, from a worker that produced nothing. Any kit whose check shells out to a compiler, a test runner, a container, or a browser must set this. Tune it to your suite: keep it as low as the slowest honest run allows, so a genuinely hung check still surfaces quickly.

## Mix with

Use `launch-kit` before this when a standalone launch page needs to be installed into a Next.js or React repo. Use `asset-swarm` after this when the new route should be captured as real footage. Use `adversarial-review` before merge when the repo change touches auth, billing, data access, or high-visibility UI.

## Gotchas

Validate against the current upstream head before merging. A worker can pass against yesterday's checkout and still be wrong after upstream moves.

Never run `git add -A` in a checkout with untracked scratch files. Stage specific paths after human review; the Ringer check only proves the worker's current diff is confined.

`engine_args` must include the repo in `sandbox_workspace_write.writable_roots`, or the worker will only be able to write its task directory.

The worker's `notes.md` belongs in the task directory, not the repo. The repo check should assert real source changes and git cleanliness; notes are just the build report.

`expect_files` is asserted before checks run. Do not put build artifacts, screenshots, or other check-produced files there.
