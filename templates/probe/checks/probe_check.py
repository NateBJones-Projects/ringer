#!/usr/bin/env python3
"""Validate one-task probe transcripts."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"FAIL: {message}")


def read_required(path: Path, label: str, failures: list[str]) -> str:
    if not path.is_file():
        failures.append(f"missing {label}: {path}")
        return ""
    if path.stat().st_size == 0:
        failures.append(f"empty {label}: {path}")
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def section_after_marker(text: str, marker: str) -> str:
    pattern = re.compile(re.escape(marker) + r"\s*(.*)", re.IGNORECASE | re.DOTALL)
    match = pattern.search(text)
    if not match:
        return ""
    value = match.group(1)
    next_heading = re.search(r"\n(?:##\s+|[A-Z][A-Z _-]{3,}:)", value)
    if next_heading:
        value = value[: next_heading.start()]
    return value.strip()


def contains_marker(haystack: str, marker: str) -> bool:
    marker = marker.strip()
    if not marker or marker.upper() == "NONE":
        return True
    return marker.lower() in haystack.lower()


def validate_model(transcript: str, combined: str, min_response_chars: int, failures: list[str]) -> None:
    # Accept either the colon markers from prompts/model-calling-probe.txt or
    # the "## Model Response Or API Result" heading that manifest.json's spec
    # asks for. Strict on substance (a non-empty response), tolerant on format.
    heading = re.search(
        r"^##\s+Model Response[^\n]*\n(.*?)(?=^##\s|\Z)",
        transcript,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    response = (
        section_after_marker(transcript, "MODEL RESPONSE:")
        or section_after_marker(transcript, "ASSISTANT:")
        or section_after_marker(transcript, "RESPONSE:")
        or (heading.group(1).strip() if heading else "")
    )
    if not response:
        failures.append(
            "model mode requires a MODEL RESPONSE:, ASSISTANT:, or RESPONSE: marker, "
            "or a '## Model Response Or API Result' heading with content under it"
        )
        return
    visible_chars = len(re.sub(r"\s+", "", response))
    if visible_chars < min_response_chars:
        failures.append(
            f"model response has {visible_chars} non-space chars, below minimum {min_response_chars}"
        )
    if "prompt:" not in transcript.lower() and "commands run" not in transcript.lower():
        failures.append("model transcript should include the prompt or command that produced the response")
    if "error" in combined.lower() and "model response" not in transcript.lower():
        failures.append("raw output contains an error but transcript does not explain a model response")


def validate_api(transcript: str, combined: str, failures: list[str]) -> None:
    lowered = combined.lower()
    if "http_status:" not in lowered and not re.search(r"\bstatus(?: code)?:\s*\d{3}", lowered):
        failures.append("api mode requires HTTP_STATUS or a status code")
    if "observed behavior:" not in transcript.lower():
        failures.append("api transcript missing OBSERVED BEHAVIOR")
    if "verdict:" not in transcript.lower():
        failures.append("api transcript missing VERDICT")


def validate_postmortem(transcript: str, failures: list[str]) -> None:
    lowered = transcript.lower()
    for marker in ("failure observed:", "evidence:", "likely cause:", "next check:", "verdict:"):
        if marker not in lowered:
            failures.append(f"postmortem transcript missing {marker}")


def validate_generic(transcript: str, failures: list[str]) -> None:
    lowered = transcript.lower()
    if "observed behavior" not in lowered and "verdict:" not in lowered:
        failures.append("generic probe needs observed behavior or verdict")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["model", "api", "postmortem", "generic"], required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--must-contain", default="NONE")
    parser.add_argument("--min-response-chars", type=int, default=120)
    args = parser.parse_args()

    failures: list[str] = []
    transcript = read_required(Path(args.transcript), "transcript", failures)
    output = read_required(Path(args.output), "raw output", failures)
    combined = f"{transcript}\n{output}"

    if transcript and not contains_marker(combined, args.must_contain):
        failures.append(f"required marker not found in transcript or output: {args.must_contain}")

    if transcript:
        mode = args.mode
        if mode == "model":
            validate_model(transcript, combined, args.min_response_chars, failures)
        elif mode == "api":
            validate_api(transcript, combined, failures)
        elif mode == "postmortem":
            validate_postmortem(transcript, failures)
        else:
            validate_generic(transcript, failures)

    if failures:
        for item in failures:
            fail(item)
        return 1

    print(f"PASS: {args.mode} probe transcript contains the required observed behavior")
    return 0


if __name__ == "__main__":
    sys.exit(main())
