#!/usr/bin/env python3
"""Validate a competitive teardown scout report."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


URL_RE = re.compile(r"https?://[^\s)\]\"'<>\\]+", re.IGNORECASE)
NUMBER_RE = re.compile(
    r"(?<![A-Za-z])(?:[$€£]?\d[\d,]*(?:\.\d+)?\s?(?:%|percent|x|k|m|b|million|billion|ms|s|seconds|minutes|hours|days|users|customers|requests|rows|records|gb|mb)?)",
    re.IGNORECASE,
)


def fail(message: str) -> None:
    print(f"FAIL: {message}")


def clean_url(raw: str) -> str:
    return raw.rstrip(".,;:!?)]}'\"")


def normalize_url(raw: str) -> str:
    raw = clean_url(raw.strip())
    parts = urlsplit(raw)
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = re.sub(r"/+", "/", parts.path).rstrip("/")
    query = parts.query.rstrip("&")
    return urlunsplit((scheme, netloc, path, query, ""))


def url_parts(raw: str) -> tuple[str, str, str]:
    normalized = normalize_url(raw)
    parts = urlsplit(normalized)
    return parts.netloc, parts.path.rstrip("/"), parts.query


def extract_urls(text: str) -> list[str]:
    return [clean_url(match.group(0)) for match in URL_RE.finditer(text)]


def split_required(value: str) -> list[str]:
    if not value.strip():
        return []
    pieces = re.split(r"[|,]", value)
    return [piece.strip() for piece in pieces if piece.strip()]


def prefix_allowed(cited: str, allowed: str) -> bool:
    cited_host, cited_path, cited_query = url_parts(cited)
    allowed_host, allowed_path, allowed_query = url_parts(allowed)
    if cited_host != allowed_host:
        return False
    if cited_query and allowed_query and cited_query != allowed_query:
        return False
    if cited_path == allowed_path:
        return True
    if not cited_path or cited_path == "/":
        return not allowed_path
    shorter, longer = sorted((cited_path, allowed_path), key=len)
    if len(shorter.strip("/")) < 8:
        return False
    return longer.startswith(shorter)


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--allowlist", required=True)
    parser.add_argument("--required-angles", default="")
    parser.add_argument("--min-words", type=int, default=500)
    parser.add_argument("--min-citations", type=int, default=3)
    parser.add_argument("--min-numbers", type=int, default=3)
    args = parser.parse_args()

    failures: list[str] = []
    report_path = Path(args.report)
    allowlist_path = Path(args.allowlist)

    if not report_path.is_file():
        failures.append(f"missing report file: {report_path}")
        for item in failures:
            fail(item)
        return 1
    if not allowlist_path.is_file():
        failures.append(f"missing allowlist file: {allowlist_path}")
        for item in failures:
            fail(item)
        return 1

    report = report_path.read_text(encoding="utf-8", errors="replace")
    allowlist = allowlist_path.read_text(encoding="utf-8", errors="replace")
    allowed_urls = sorted(set(extract_urls(allowlist)))
    cited_urls = sorted(set(extract_urls(report)))

    if not allowed_urls:
        failures.append("allowlist contains no URLs")
    if len(cited_urls) < args.min_citations:
        failures.append(
            f"report cites {len(cited_urls)} URL(s), below required minimum {args.min_citations}"
        )

    disallowed = [
        url
        for url in cited_urls
        if not any(prefix_allowed(url, allowed) for allowed in allowed_urls)
    ]
    if disallowed:
        failures.append(
            "report cites URL(s) outside the allowlist: " + ", ".join(disallowed[:10])
        )

    words = word_count(report)
    if words < args.min_words:
        failures.append(f"report has {words} words, below required minimum {args.min_words}")

    numbers = sorted(set(NUMBER_RE.findall(report)))
    if len(numbers) < args.min_numbers:
        failures.append(
            f"report has {len(numbers)} numeric fact(s), below required minimum {args.min_numbers}"
        )

    lowered = report.lower()
    for angle in split_required(args.required_angles):
        if angle.lower() not in lowered:
            failures.append(f"missing required angle: {angle}")

    for section in ("## Target", "## Source Log", "## Angle Findings", "## Extracted Numbers"):
        if section.lower() not in lowered:
            failures.append(f"missing required section: {section}")

    if "could not fetch" not in lowered and "fetched" not in lowered:
        failures.append("report does not record fetch status for sources")

    if failures:
        for item in failures:
            fail(item)
        return 1

    print(
        "PASS: teardown report cites only allowlisted URLs "
        f"({len(cited_urls)} cited), covers required angles, and contains {len(numbers)} numeric facts"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
