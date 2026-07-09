#!/usr/bin/env python3
"""Regenerate tests/fixtures/design-reference.html from the current renderer.

The fixture is the design-approved reference page that
tests/test_design_reference.py compares the renderer's CSS tokens against.
Re-run this ONLY when a design change is intentional, review the diff, and
commit the result — the fixture is the frozen "approved look", so silently
regenerating it defeats the test.

Usage: python3 tests/fixtures/make_design_reference.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ringer import render_final_report_html  # noqa: E402

# Representative finished-run state: one of each verdict so the reference
# page shows the full status palette (same shapes as test_design_reference).
STATE = {
    "run_id": "design-reference",
    "run_name": "Design Reference",
    "identity": "design-bake",
    "state": "finished",
    "started_at": "2026-07-05T00:00:00+00:00",
    "elapsed_s": 92,
    "finished": True,
    "report_ready": False,
    "report_path": None,
    "tasks": [
        {
            "key": "task-pass",
            "status": "pass",
            "attempts": 1,
            "elapsed_s": 41,
            "check_output_tail": "all checks green",
        },
        {
            "key": "task-retry-pass",
            "status": "pass",
            "attempts": 2,
            "elapsed_s": 77,
            "check_output_tail": "recovered on retry",
        },
        {
            "key": "task-fail",
            "status": "fail",
            "attempts": 2,
            "elapsed_s": 63,
            "check_output_tail": "FAIL: expected section missing",
        },
    ],
}


def main() -> int:
    target = Path(__file__).resolve().parent / "design-reference.html"
    html = render_final_report_html(STATE)
    # The footer stamps render wall-clock time; pin it so re-bakes diff clean.
    html = re.sub(r"Finished \d{2}:\d{2}:\d{2} [A-Z]+", "Finished 12:00:00 UTC", html)
    target.write_text(html, encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
