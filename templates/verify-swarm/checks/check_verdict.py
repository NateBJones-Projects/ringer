#!/usr/bin/env python3
"""Validate one hostile-verifier verdict report (verify-swarm kit)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="path to the verdict report")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"FAIL: missing report: {path}")
        return 1
    if path.stat().st_size == 0:
        print(f"FAIL: empty report: {path}")
        return 1

    text = path.read_text(encoding="utf-8", errors="replace")
    lowered = text.lower()
    failures: list[str] = []

    # Verdict lines only — quoting CONFIRMED/REFUTED elsewhere is fine.
    verdict_hits = re.findall(
        r"(?im)^\s*(?:#+\s*)?verdict\s*:?\s*.*?\b(confirmed|refuted)\b", text
    )
    if not verdict_hits:
        failures.append("no Verdict line containing CONFIRMED or REFUTED")
    elif len({v.lower() for v in verdict_hits}) > 1:
        failures.append("contradictory Verdict lines: both CONFIRMED and REFUTED present")

    if "evidence" not in lowered:
        failures.append("no Evidence section")

    if not re.search(r"[\w./-]+\.(?:rs|py|md|toml|json|sh|yml|yaml)(?::\d+)?", text):
        failures.append("no concrete file citation (path.ext or path.ext:line) anywhere in report")

    if "reasoning" not in lowered:
        failures.append("no Reasoning section")

    if re.search(r"(?i)\bi\s+(edited|modified|patched|fixed|rewrote)\b", text):
        failures.append("report claims the verifier modified files; verifiers are read-only")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("OK: verdict report structurally valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
