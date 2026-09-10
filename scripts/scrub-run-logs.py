#!/usr/bin/env python3
"""Scrub credentials already written to disk in a Ringer state directory.

Applies ``ringer.redact_secrets`` to every place a state dir persists
captured worker output: ``runs/*.json``, the ``runs.jsonl`` model log,
the generated ``artifacts/**/*.html`` pages, and ``work/**/logs/*.log``.
JSON and JSON Lines are redacted through their PARSED structure so the
escaping cannot be broken; everything else is redacted as text.

Dry-run by default: without ``--write`` it only reports what would change.
After rewriting the model log it resets the scoreboard's cached read
cursor, which the rewrite invalidates.

Usage:
    python3 scripts/scrub-run-logs.py [--state-dir PATH] [--write]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import redact_secrets  # noqa: E402


def target_files(state_dir: Path) -> list[Path]:
    """Every place a Ringer state dir persists captured worker output.

    All four are needed. The first sweep covered only runs/*.json and the
    worker logs, and left credentials sitting in runs.jsonl and in the
    artifact HTML — which is the copy a browser actually renders.
    """
    files: list[Path] = []
    runs_dir = state_dir / "runs"
    if runs_dir.is_dir():
        files.extend(sorted(runs_dir.glob("*.json")))
    # The model log: JSON Lines, one record per line, NOT one document.
    runs_jsonl = state_dir / "runs.jsonl"
    if runs_jsonl.is_file():
        files.append(runs_jsonl)
    # Generated artifact pages embed check output and are served to a browser.
    artifacts_dir = state_dir / "artifacts"
    if artifacts_dir.is_dir():
        files.extend(sorted(artifacts_dir.glob("**/*.html")))
    work_dir = state_dir / "work"
    if work_dir.is_dir():
        files.extend(sorted(work_dir.glob("**/logs/*.log")))
    return files


def redact_json_value(value: object) -> object:
    """Recursively redact every string INSIDE a parsed JSON document."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [redact_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_json_value(item) for key, item in value.items()}
    return value


def scrub_file(path: Path, *, write: bool) -> int:
    """Return the number of replacements made (0 if the file is unchanged).

    A run JSON is redacted through its PARSED structure, never as raw text.
    Run records embed worker specs as JSON strings whose inner quotes are
    backslash-escaped; a text-level regex sees `\\"token\\": \\"value\\"`,
    rewrites across the escaping, and leaves a file that no longer parses.
    Two real run records were corrupted this way in a dry-run rehearsal
    before this was caught. Structural redaction cannot break escaping,
    because json.dumps re-escapes on the way out.
    """
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    if path.suffix == ".json":
        try:
            document = json.loads(original)
        except (ValueError, RecursionError):
            # Not valid JSON despite the extension (truncated by a crash, say).
            # Fall through to text mode: there is no structure left to protect.
            redacted = redact_secrets(original)
        else:
            redacted = json.dumps(redact_json_value(document), indent=2) + "\n"
    elif path.suffix == ".jsonl":
        # One JSON document PER LINE. Redact each line structurally and keep
        # it on its own line; a malformed line is redacted as text and kept
        # rather than dropped, so the log never loses a record.
        out_lines: list[str] = []
        for line in original.splitlines():
            if not line.strip():
                out_lines.append(line)
                continue
            try:
                out_lines.append(json.dumps(redact_json_value(json.loads(line))))
            except (ValueError, RecursionError):
                out_lines.append(redact_secrets(line))
        redacted = "\n".join(out_lines) + ("\n" if original.endswith("\n") else "")
    else:
        redacted = redact_secrets(original)

    if redacted == original:
        return 0
    replacements = redacted.count("[REDACTED]") - original.count("[REDACTED]")
    if replacements <= 0:
        # A pure reformat with no new redactions is not worth rewriting.
        return 0
    if write:
        path.write_text(redacted, encoding="utf-8")
    return replacements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path.home() / ".ringer",
        help="Ringer state directory to scrub (default: ~/.ringer)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually rewrite files. Without this flag, only report what would change.",
    )
    args = parser.parse_args(argv)

    state_dir: Path = args.state_dir
    files = target_files(state_dir)

    files_scanned = 0
    files_with_secrets = 0
    total_replacements = 0
    rewrote_model_log = False

    for path in files:
        files_scanned += 1
        replacements = scrub_file(path, write=args.write)
        if replacements:
            if path.name == "runs.jsonl":
                rewrote_model_log = True
            files_with_secrets += 1
            total_replacements += replacements
            verb = "rewrote" if args.write else "would rewrite"
            rel = path.relative_to(state_dir) if path.is_relative_to(state_dir) else path
            print(f"{verb}: {rel} ({replacements} replacement{'s' if replacements != 1 else ''})")

    mode = "write" if args.write else "dry-run"
    print(
        f"[{mode}] files scanned: {files_scanned}, "
        f"files with secrets: {files_with_secrets}, "
        f"replacements: {total_replacements}"
    )

    if args.write and rewrote_model_log:
        reset_model_log_offset(state_dir)

    return 0


def reset_model_log_offset(state_dir: Path) -> None:
    """Force the model read-model to rebuild after runs.jsonl is rewritten.

    The scoreboard reads runs.jsonl incrementally from a cached BYTE OFFSET
    kept in ringer.db. Rewriting the log changes every offset after the first
    redaction, so a stale cursor resumes mid-record: the observed symptom was
    the scoreboard reporting "894 rows, 1 skipped lines" against a file whose
    895 records all parse cleanly. There is a `log_size < offset` guard, but
    it only catches the log getting SHORTER — redaction can leave it longer.
    Zeroing the cursor makes the next read rebuild from the top.
    """
    db_path = state_dir / "ringer.db"
    if not db_path.is_file():
        return
    try:
        import sqlite3

        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "UPDATE sync_state SET value = '0' WHERE key = 'log_offset'"
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        print(
            f"WARNING: could not reset the model-log cursor in {db_path} "
            f"({type(exc).__name__}: {exc}). Run './ringer.py db rebuild' by hand, "
            "or the scoreboard will resume mid-record and drop rows."
        )
        return
    print("reset the model-log cursor; the scoreboard rebuilds on next read")


if __name__ == "__main__":
    sys.exit(main())
