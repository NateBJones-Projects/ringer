# Referee

Referee is a Claude Code function-hooks plugin that shows Ringer beside the chat.
It reads Ringside's `/api/runs` endpoint. Ringside supplies run state and active-run membership.

## Install locally

Use a Claude Code build with function hooks and Pane support. From the Ringer
checkout, start Ringside and load the plugin:

```sh
./ringer.py hud --no-open
claude --plugin-dir ./referee
```

`/referee open` opens the pane. `/referee close` closes it. `/referee status`
returns a one-line summary. The terminal docks the pane in fullscreen from 110
columns; narrower terminals request eight rows above the prompt, including the
frame's two border rows. Inline content fits the available body rows, with the
header and error notices included in that budget. When tasks do not all fit,
the last row says `+N more` and counts every omitted task. A minimized widget
shows one line. Long text is truncated to the pane's body width.

## Options

Set these plugin userConfig fields through Claude Code's plugin settings:

| Field | Default | Meaning |
| --- | --- | --- |
| `port` | `8700` | Ringside port on `127.0.0.1` (1-65535). |
| `pollMs` | `2000` | Poll interval in milliseconds (minimum 100). |
| `paneColumns` | `60` | Requested dock width (minimum 24). |
| `openOnStart` | `true` | Open the pane at session start. |
| `statusLine` | `true` | Poll and show a live-run status summary, even with the pane closed. |

Polling runs while the pane is open or `statusLine` is enabled. Disable the
status line and close the pane to stop HTTP requests.

## Reading the pane

The newest run with `state: "live"` and a `run_id` in `active` wins.
`+N more live` counts the others. With no live run, the newest finished run stays
visible for 30 minutes as **last run**. The API has no finish timestamp, so this
window uses `started_at + elapsed_s` when both fields are available. If neither
selection applies, only the first run in server order (the most recently written
state file) can appear as **STOPPED** in the error colour: it must have
`state: "died"`, or `state: "live"` with no entry in `active`.
Otherwise the pane says **no live run**.

Task statuses, including `verifying` and unfamiliar values, come from Ringside.
Stopped runs label their task statuses as saved and draw lanes in the error
colour; those statuses describe the last capture, not ongoing work.
No verdict is inferred from an exit code.
There are no per-attempt records: each cell before the last is a failed attempt,
because Ringer retries only after a failed check. The last cell uses the task's
status: green for pass, red for fail, an outlined warning cell for an unfinished
attempt. Stopped unfinished cells use the error colour.

The finished receipt shows duration, pass/fail counts, retries, and tokens.
Failed lanes show the last non-empty line of `check_output_tail`. Unknown tokens
show `-`; missing models leave the engine name alone. Missing durations and
attempt counts show `-`. Without `max_attempts`, the attempt count appears alone.
Without `check_output_tail`, there is no failure excerpt.

After three polling intervals without success, the header says **STALE** and all
rows dim. A connection failure includes the port and Ringside startup command.
Cached verdicts remain visible. ANSI escapes and control characters are stripped
from Ringside text before drawing.

## What it does not do

Referee does not launch workers, retry tasks, or change Ringer
state. Commands displayed in the pane are instructions for you to run. It never
reads `~/.ringer` files or derives a pass from an exit code. It makes only local
HTTP GET requests and does not use subprocesses or filesystem writes.

Known limit: a SIGKILLed orchestrator stays in `active`, so its run still reads
**LIVE**, exactly as Ringside shows it. `api-runs-killed.json` captures this case.
Referee does not guess whether that process is alive. Each run requires string
`run_id`, `run_name`, and `state` fields, plus a `tasks` array whose entries have
string `key` and `status` fields. Other fields are optional. Unreadable runs are
skipped, with a warning count in the dock. Invalid JSON or a missing `runs` array
or `active` object shows a contract error and preserves the last successful snapshot.

## Checks

```sh
claude plugin validate referee
claude plugin test referee
python3 -m unittest tests.test_referee_contract
python3 -m unittest discover -s tests
```

The Mod runner cannot import JSON or read fixture files. Its test module embeds
the captured response text; the Python test verifies those copies against the
read-only fixtures. UI tests exercise all four surfaces, three placements, and
widths of 24, 40, 60, and 90 columns. They validate render trees, not terminal or
app pixels. Inline tests also check body heights of 3, 4, and 6 rows, including
cached errors, and account for every task drawn or counted in `+N more`.
The terminal renders the Ringside URL as a link; remote surfaces
show plain text because their link policy rejects HTTP URLs on `127.0.0.1`.
