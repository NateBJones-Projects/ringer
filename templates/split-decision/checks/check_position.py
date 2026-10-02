#!/usr/bin/env python3
"""Validate one independent position block for a split-decision sweep."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys


METADATA_LABELS = ["agent", "model", "provider", "stance", "summary"]
STANCES = {"recommend", "oppose", "alternative", "abstain"}
MIN_REASONING_CHARS = 400


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="position.md")
    args = parser.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"FAIL: {path} not found")
        return 1
    text = path.read_text(encoding="utf-8", errors="replace")
    fails: list[str] = []

    headings = re.findall(r"(?im)^#+\s*position\s*:", text)
    if len(headings) == 0:
        fails.append("missing '### Position:' heading")
    elif len(headings) > 1:
        fails.append(
            f"{len(headings)} position blocks found; one worker writes exactly one "
            "position — a second block usually means another participant's output leaked in"
        )

    for label in METADATA_LABELS:
        if not re.search(rf"(?im)^\s*-\s*{label}\s*:\s*\S", text):
            fails.append(f"missing '- {label}:' metadata line")

    stance = re.search(r"(?im)^\s*-\s*stance\s*:\s*([a-z]+)", text)
    if stance and stance.group(1).lower() not in STANCES:
        fails.append(
            f"stance '{stance.group(1)}' is not one of: {', '.join(sorted(STANCES))}"
        )

    summary = re.search(r"(?im)^\s*-\s*summary\s*:\s*(.+)$", text)
    if summary and len(summary.group(1).strip()) < 15:
        fails.append("summary is too thin; one real sentence stating the position")

    metadata_end = 0
    for match in re.finditer(r"(?im)^\s*-\s*(?:agent|model|provider|stance|summary)\s*:.*$", text):
        metadata_end = max(metadata_end, match.end())
    reasoning = text[metadata_end:].strip()
    if len(reasoning) < MIN_REASONING_CHARS:
        fails.append(
            f"reasoning after the metadata lines is {len(reasoning)} chars; "
            f"need at least {MIN_REASONING_CHARS} of substantive argument"
        )

    if stance and stance.group(1).lower() == "abstain" and reasoning:
        if not re.search(r"(?i)\b(missing|unknown|unclear|insufficient|cannot|can't)\b", reasoning):
            fails.append("an abstain position must say exactly what information is missing")

    if fails:
        print("FAIL:")
        for fail in fails:
            print(f" - {fail}")
        return 1
    print(f"PASS: one position block, stance '{stance.group(1).lower() if stance else '?'}', "
          f"{len(reasoning)} chars of reasoning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
