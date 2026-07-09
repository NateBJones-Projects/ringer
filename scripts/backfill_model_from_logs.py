#!/usr/bin/env python3
"""Backfill missing eval-row models from recorded worker command lines.

The eval log is rewritten only when command-log evidence is found. Run-state
files and worker logs are read-only inputs.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COMMAND_PREFIX = "[ringer.py] command: "
BACKFILL_NOTE = "\nmodel_backfill=command_log"


def _split_raw_line(raw: bytes) -> tuple[bytes, bytes]:
    """Return a line's body and terminator without changing either."""
    if raw.endswith(b"\r\n"):
        return raw[:-2], b"\r\n"
    if raw.endswith(b"\n"):
        return raw[:-1], b"\n"
    return raw, b""


def _model_from_tokens(tokens: list[str]) -> str | None:
    model: str | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ("-m", "--model"):
            if (
                index + 1 < len(tokens)
                and tokens[index + 1]
                and not tokens[index + 1].startswith("-")
            ):
                model = tokens[index + 1]
                index += 2
                continue
        elif token.startswith("--model="):
            value = token.split("=", 1)[1]
            if value:
                model = value
        index += 1
    return model


def model_from_command_log(log_path: Path) -> tuple[str | None, str | None]:
    """Return the model from the last Ringer command line in the log."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return None, f"could not read log file: {exc}"

    model: str | None = None
    last_command_parsed = False
    command_lines = 0
    malformed_commands = 0
    for line in lines:
        if not line.startswith(COMMAND_PREFIX):
            continue
        command_lines += 1
        command_text = line[len(COMMAND_PREFIX):]
        try:
            tokens = shlex.split(command_text)
        except ValueError:
            malformed_commands += 1
            last_command_parsed = False
            model = None
            continue
        last_command_parsed = True
        model = _model_from_tokens(tokens)

    if model is not None:
        return model, None
    if command_lines == 0:
        return None, "log has no [ringer.py] command line"
    if not last_command_parsed:
        return None, "last command line could not be parsed with shlex"
    if malformed_commands == command_lines:
        return None, "all command lines could not be parsed with shlex"
    return None, "command lines contain no -m/--model flag"


def _load_task_log_path(state_path: Path, task_key: Any) -> tuple[Path | None, str | None]:
    if not state_path.is_file():
        return None, f"run-state file missing: {state_path}"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"could not read run-state file: {exc}"
    if not isinstance(state, dict) or not isinstance(state.get("tasks"), list):
        return None, "run-state file has no tasks list"

    task = next(
        (
            item
            for item in state["tasks"]
            if isinstance(item, dict) and item.get("key") == task_key
        ),
        None,
    )
    if task is None:
        return None, f"task not found in run state: {task_key!r}"
    log_path = task.get("log_path")
    if not isinstance(log_path, str) or not log_path:
        return None, "matching task has no log_path"
    path = Path(log_path).expanduser()
    if not path.is_file():
        return None, f"log file missing: {path}"
    return path, None


def process_lines(raw_lines: list[bytes], state_dir: Path) -> tuple[list[bytes], dict[str, int]]:
    """Build rewritten lines and print a report for every input row."""
    out_lines = list(raw_lines)
    counts = {"stamped": 0, "no_evidence": 0, "already_attributed": 0}

    for line_number, raw in enumerate(raw_lines, start=1):
        body, newline = _split_raw_line(raw)
        try:
            row = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            print(f"line {line_number}: skipped; unparseable line preserved")
            continue
        if not isinstance(row, dict):
            print(f"line {line_number}: skipped; JSON value is not an object")
            continue

        current_model = row.get("model")
        if isinstance(current_model, str) and current_model:
            counts["already_attributed"] += 1
            print(f"line {line_number}: already attributed: {current_model}")
            continue

        run_id = row.get("run_id")
        task_key = row.get("task_key")
        if not isinstance(run_id, str) or not run_id:
            counts["no_evidence"] += 1
            print(f"line {line_number}: skipped; missing run_id")
            continue

        state_path = state_dir / "runs" / f"{run_id}.json"
        log_path, reason = _load_task_log_path(state_path, task_key)
        if log_path is None:
            counts["no_evidence"] += 1
            print(f"line {line_number} ({run_id}/{task_key}): skipped; {reason}")
            continue

        model, reason = model_from_command_log(log_path)
        if model is None:
            counts["no_evidence"] += 1
            print(f"line {line_number} ({run_id}/{task_key}): skipped; {reason}")
            continue

        old_display = repr(current_model) if "model" in row else "<missing>"
        row["model"] = model
        notes = row.get("notes")
        row["notes"] = (notes if isinstance(notes, str) else "") + BACKFILL_NOTE
        out_lines[line_number - 1] = (
            json.dumps(row, ensure_ascii=False).encode("utf-8") + newline
        )
        counts["stamped"] += 1
        print(
            f"line {line_number} ({run_id}/{task_key}): "
            f"model {old_display} -> {model!r}"
        )

    return out_lines, counts


def _atomic_rewrite(path: Path, lines: list[bytes]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup_path)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return backup_path


def _print_totals(counts: dict[str, int]) -> None:
    print(
        "totals: "
        f"stamped={counts['stamped']} "
        f"no-evidence={counts['no_evidence']} "
        f"already-attributed={counts['already_attributed']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill missing models using recorded worker command lines."
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("~/.ringer"),
        help="Ringer state directory (default: ~/.ringer)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report changes without writing runs.jsonl or a backup",
    )
    args = parser.parse_args(argv)

    state_dir = args.state_dir.expanduser().resolve()
    log_path = state_dir / "runs.jsonl"
    if not log_path.is_file():
        print(f"error: eval log not found: {log_path}", file=sys.stderr)
        return 1

    try:
        raw_lines = log_path.read_bytes().splitlines(keepends=True)
    except OSError as exc:
        print(f"error: could not read eval log: {exc}", file=sys.stderr)
        return 1

    out_lines, counts = process_lines(raw_lines, state_dir)

    if args.dry_run:
        print("dry-run: no files written")
    elif counts["stamped"]:
        try:
            backup_path = _atomic_rewrite(log_path, out_lines)
        except OSError as exc:
            print(f"error: could not rewrite eval log: {exc}", file=sys.stderr)
            return 1
        print(f"backup: {backup_path}")
    else:
        print("no changes; runs.jsonl was not rewritten")

    _print_totals(counts)
    if not args.dry_run:
        print(
            "refresh the derived read model: "
            f"python3 ringer.py db rebuild --log {shlex.quote(str(log_path))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
