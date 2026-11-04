#!/usr/bin/env python3
"""Validate one claim-research result against a local source packet.

This checker validates completeness, schema, statuses, and literal evidence. It
does not decide whether a supported, unsupported, or unclear verdict is
intellectually correct; that judgment belongs in the human blind review.

The script uses only the Python standard library and makes no network calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ALLOWED_STATUSES = {"supported", "unsupported", "unclear"}
REQUIRED_FIELDS = {"claim_id", "status", "source", "quote", "explanation"}
PASS_MESSAGE = (
    "PASS: every requested claim is present, the schema is valid, and all "
    "cited evidence appears in an allowed source"
)


def failure(claim_id: str, detail: str) -> str:
    """Return one consistently formatted, actionable failure."""
    return f"FAIL [{claim_id}]: {detail}"


def load_json(path: Path, label: str) -> tuple[Any | None, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return None, [failure(label, f"file does not exist: {path}")]
    except OSError as exc:
        return None, [failure(label, f"could not read {path}: {exc}")]
    except json.JSONDecodeError as exc:
        return None, [
            failure(
                label,
                f"invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            )
        ]


def extract_rows(payload: Any, label: str) -> tuple[list[Any], list[str]]:
    if not isinstance(payload, dict):
        return [], [failure(label, "top-level JSON value must be an object")]
    rows = payload.get("claims")
    if not isinstance(rows, list):
        return [], [failure(label, "top-level 'claims' field must be an array")]
    return rows, []


def normalize_whitespace(text: str) -> str:
    """Collapse whitespace without changing any non-whitespace character."""
    return " ".join(text.split())


def source_path(sources_dir: Path, source_name: str) -> Path | None:
    """Resolve an allowed source name without permitting directory traversal."""
    root = sources_dir.resolve()
    candidate = (root / source_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def validate_claim_definitions(rows: list[Any]) -> tuple[dict[str, str], list[str]]:
    requested: dict[str, str] = {}
    failures: list[str] = []

    for index, row in enumerate(rows, start=1):
        label = f"claims row {index}"
        if not isinstance(row, dict):
            failures.append(failure(label, "claim definition must be an object"))
            continue

        claim_id = row.get("id")
        claim_text = row.get("claim")
        if not isinstance(claim_id, str) or not claim_id.strip():
            failures.append(failure(label, "field 'id' must be a nonempty string"))
            continue
        claim_id = claim_id.strip()
        if not isinstance(claim_text, str) or not claim_text.strip():
            failures.append(
                failure(claim_id, "claim definition field 'claim' must be a nonempty string")
            )
        if claim_id in requested:
            failures.append(failure(claim_id, "claim id is duplicated in claims file"))
            continue
        requested[claim_id] = claim_text if isinstance(claim_text, str) else ""

    return requested, failures


def validate_result_rows(
    rows: list[Any], requested: dict[str, str], sources_dir: Path
) -> list[str]:
    failures: list[str] = []
    seen: dict[str, int] = {}

    for index, row in enumerate(rows, start=1):
        row_label = f"result row {index}"
        if not isinstance(row, dict):
            failures.append(failure(row_label, "row must be an object"))
            continue

        raw_claim_id = row.get("claim_id")
        claim_id = (
            raw_claim_id.strip()
            if isinstance(raw_claim_id, str) and raw_claim_id.strip()
            else row_label
        )

        missing_fields = sorted(REQUIRED_FIELDS - row.keys())
        if missing_fields:
            failures.append(
                failure(
                    claim_id,
                    "missing required field(s): " + ", ".join(missing_fields),
                )
            )

        if not isinstance(raw_claim_id, str) or not raw_claim_id.strip():
            failures.append(
                failure(row_label, "required field 'claim_id' must be a nonempty string")
            )
        else:
            normalized_id = raw_claim_id.strip()
            seen[normalized_id] = seen.get(normalized_id, 0) + 1
            if normalized_id not in requested:
                failures.append(failure(normalized_id, "unknown claim id"))

        status = row.get("status")
        source = row.get("source")
        quote = row.get("quote")
        explanation = row.get("explanation")

        if not isinstance(status, str) or status not in ALLOWED_STATUSES:
            failures.append(
                failure(
                    claim_id,
                    "invalid status; expected one of: supported, unsupported, unclear",
                )
            )
            continue

        for field_name, value in (
            ("source", source),
            ("quote", quote),
            ("explanation", explanation),
        ):
            if value is not None and not isinstance(value, str):
                failures.append(
                    failure(
                        claim_id,
                        f"field '{field_name}' must be a string or null",
                    )
                )

        source_text = source.strip() if isinstance(source, str) else ""
        quote_text = quote.strip() if isinstance(quote, str) else ""
        explanation_text = explanation.strip() if isinstance(explanation, str) else ""

        if status == "unsupported":
            if source_text:
                failures.append(
                    failure(
                        claim_id,
                        "unsupported claim must not include a source citation",
                    )
                )
            if quote_text:
                failures.append(
                    failure(
                        claim_id,
                        "unsupported claim must not include a quoted passage",
                    )
                )

        if status == "unclear" and not explanation_text:
            failures.append(
                failure(
                    claim_id,
                    "unclear claim needs an explanation of what the sources do not establish",
                )
            )

        if status == "supported":
            if not source_text:
                failures.append(
                    failure(claim_id, "supported claim must name a source file")
                )
            if not quote_text:
                failures.append(
                    failure(claim_id, "supported claim must include a quoted passage")
                )

        has_citation = bool(source_text or quote_text)
        complete_citation = bool(source_text and quote_text)
        if status == "unclear" and has_citation and not complete_citation:
            failures.append(
                failure(
                    claim_id,
                    "a citation must include both 'source' and 'quote'",
                )
            )

        if complete_citation and status in {"supported", "unclear"}:
            candidate = source_path(sources_dir, source_text)
            if candidate is None:
                failures.append(
                    failure(
                        claim_id,
                        f"source '{source_text}' is outside the allowed sources directory",
                    )
                )
                continue
            if not candidate.is_file():
                failures.append(
                    failure(
                        claim_id,
                        f"cited source file does not exist: {source_text}",
                    )
                )
                continue

            try:
                document = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                failures.append(
                    failure(claim_id, f"could not read cited source '{source_text}': {exc}")
                )
                continue

            normalized_quote = normalize_whitespace(quote_text)
            normalized_document = normalize_whitespace(document)
            if not normalized_quote or normalized_quote not in normalized_document:
                failures.append(
                    failure(
                        claim_id,
                        f"quoted passage was not found verbatim in source '{source_text}' "
                        "after whitespace normalization",
                    )
                )

    for claim_id, count in sorted(seen.items()):
        if claim_id in requested and count != 1:
            failures.append(
                failure(
                    claim_id,
                    f"requested claim appears {count} times; expected exactly once",
                )
            )

    for claim_id in requested:
        if claim_id not in seen:
            failures.append(
                failure(claim_id, "requested claim is missing from the result")
            )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a claim-research result against local claims and sources."
    )
    parser.add_argument("result", type=Path, help="result JSON to validate")
    parser.add_argument("--claims", required=True, type=Path, help="requested claims JSON")
    parser.add_argument(
        "--sources", required=True, type=Path, help="directory containing allowed sources"
    )
    args = parser.parse_args()

    failures: list[str] = []
    claims_payload, claim_load_failures = load_json(args.claims, "claims file")
    result_payload, result_load_failures = load_json(args.result, "result file")
    failures.extend(claim_load_failures)
    failures.extend(result_load_failures)

    if not args.sources.is_dir():
        failures.append(
            failure("sources directory", f"directory does not exist: {args.sources}")
        )

    if failures:
        for item in failures:
            print(item)
        return 1

    claim_rows, claim_shape_failures = extract_rows(claims_payload, "claims file")
    result_rows, result_shape_failures = extract_rows(result_payload, "result file")
    failures.extend(claim_shape_failures)
    failures.extend(result_shape_failures)

    requested, definition_failures = validate_claim_definitions(claim_rows)
    failures.extend(definition_failures)

    if not claim_shape_failures and not result_shape_failures and args.sources.is_dir():
        failures.extend(validate_result_rows(result_rows, requested, args.sources))

    if failures:
        for item in failures:
            print(item)
        return 1

    print(PASS_MESSAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
