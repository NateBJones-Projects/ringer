#!/usr/bin/env python3
"""Validate a second-phase competitive teardown synthesis."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


URL_RE = re.compile(r"https?://[^\s)\]\"'<>\\]+", re.IGNORECASE)


def fail(message: str) -> None:
    print(f"FAIL: {message}")


def split_paths(value: str) -> list[Path]:
    pieces = re.split(r"[|,]", value)
    return [Path(piece.strip()) for piece in pieces if piece.strip()]


def extract_urls(text: str) -> set[str]:
    return {match.group(0).rstrip(".,;:!?)]}'\"") for match in URL_RE.finditer(text)}


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--scout-reports", required=True)
    parser.add_argument("--min-competitors", type=int, default=2)
    parser.add_argument("--min-words", type=int, default=400)
    args = parser.parse_args()

    failures: list[str] = []
    report_path = Path(args.report)
    scout_paths = split_paths(args.scout_reports)

    if not report_path.is_file():
        failures.append(f"missing synthesis report: {report_path}")
    if len(scout_paths) < args.min_competitors:
        failures.append(
            f"only {len(scout_paths)} scout report path(s) supplied; expected at least {args.min_competitors}"
        )

    scout_texts: list[str] = []
    for path in scout_paths:
        if not path.is_file():
            failures.append(f"missing scout report: {path}")
            continue
        scout_texts.append(path.read_text(encoding="utf-8", errors="replace"))

    if failures:
        for item in failures:
            fail(item)
        return 1

    report = report_path.read_text(encoding="utf-8", errors="replace")
    lowered = report.lower()
    for section in (
        "## decision summary",
        "## comparison table",
        "## strongest evidence",
        "## gaps and could-not-fetch items",
        "## recommended follow-up",
    ):
        if section not in lowered:
            failures.append(f"missing required section: {section}")

    if word_count(report) < args.min_words:
        failures.append(f"synthesis is below minimum substance threshold of {args.min_words} words")

    allowed_urls = set().union(*(extract_urls(text) for text in scout_texts))
    report_urls = extract_urls(report)
    new_urls = sorted(url for url in report_urls if url not in allowed_urls)
    if new_urls:
        failures.append(
            "synthesis cites URL(s) not present in scout reports: " + ", ".join(new_urls[:10])
        )
    if not report_urls:
        failures.append("synthesis contains no source URLs from scout reports")

    if failures:
        for item in failures:
            fail(item)
        return 1

    print(
        "PASS: synthesis compares supplied scout reports and cites only URLs already present in them"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
