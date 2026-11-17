Real `/api/runs` responses, captured from a running Ringside on this repo's `ringer.py`.

- `api-runs-empty.json`, `-live.json`, `-finished.json`, `-killed.json`: an isolated state dir and the mock engine (`engines/mock_worker.py`). The live capture has a lane mid-check and a lane that failed twice. The killed capture is a run whose orchestrator got SIGKILL mid-check: its state still says `live` and it is still listed in `active`.
- `api-runs-codex-finished.json`: a real two-lane Codex run with its text fields and paths scrubbed. Numbers, statuses, models and attempts are as recorded.
- `api-runs-legacy.json`: a real run from July 2026, written by an older Ringer whose tasks have no `max_attempts`. Text fields and paths scrubbed.

Local paths are rewritten to `/tmp/ringer-fixtures` and `/opt/ringer`.
