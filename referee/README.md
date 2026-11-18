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
columns; narrower terminals request fourteen rows above the prompt, including the
frame's two border rows. Inline content fits the available body rows, with the
header and error notices included in that budget. When tasks do not all fit,
the last row says `+N more` and counts every omitted task. A minimized widget
shows one line. Long text is truncated to the pane's body width.

## Options

Set these plugin userConfig fields through Claude Code's plugin settings:

| Field | Default | Meaning |
| --- | --- | --- |
| `port` | `8700` | Ringside port on `127.0.0.1` (1-65535). |
| `pollMs` | `1000` | Poll interval in milliseconds (minimum 100). |
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

The orange run card pairs the run name with its live, finished, or stopped badge.
It shows elapsed time, lane and parallel counts, tokens, and a stacked status bar.
Wide docks put the card beside the lanes. Blue marks the lanes header and Ringside link.

Failed lanes come first, then running lanes, then the rest in manifest order.
Green means pass, red fail, amber running, and muted gray queued. Lane bars compare
elapsed durations with the longest lane; they are not a timeline. Attempt cells
and clocks align on the right. Each cell before the last is a failed attempt;
the last reflects the captured status. No verdict is inferred from an exit code.
Stopped unfinished lanes use a red hollow circle and say “stopped before it finished.”

A failed lane shows its last non-empty check output line underneath. A running
lane shows its latest activity. Inline includes these details when space allows;
its last row counts omitted lanes and groups their names by status. With spare
room, a blank row separates the header from the lanes.

Finished cards add a receipt with retries and tokens. Unknown values show `-`,
including an unknown attempt maximum (`2/-`). Text truncates instead of wrapping.

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
widths from 24 through 160 columns. They validate render trees, not terminal or
app pixels. Inline tests cover tiny budgets, the 6-, 8-, and 14-row layouts, and
cached errors, and account for every task drawn or counted in `+N more`.
The terminal renders the Ringside URL as a link; remote surfaces
show plain text because their link policy rejects HTTP URLs on `127.0.0.1`.
