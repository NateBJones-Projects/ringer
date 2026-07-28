#!/usr/bin/env python3
"""Select a small, source-backed packet without calling a model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


def find_ringer_root() -> Path:
    """Find the checkout that owns context_packet.py."""
    starting_points = (Path(__file__).resolve(), Path.cwd().resolve())
    checked: set[Path] = set()
    for start in starting_points:
        candidates = (start, *start.parents)
        for candidate in candidates:
            if candidate in checked:
                continue
            checked.add(candidate)
            if (candidate / "context_packet.py").is_file():
                return candidate
    raise RuntimeError(
        "Could not find context_packet.py. Run this script from a Ringer checkout "
        "or keep the token-saver skill inside that checkout."
    )


def read_required_text(*, direct: str | None, file_path: Path | None, label: str) -> str:
    if direct is not None:
        value = direct
    elif file_path is not None:
        value = file_path.expanduser().read_text(encoding="utf-8")
    else:
        raise ValueError(f"{label} is required")
    value = value.strip()
    if not value:
        raise ValueError(f"{label} must not be empty")
    return value


def write_text(path: Path | None, value: str) -> None:
    if path is None:
        sys.stdout.write(value)
        return
    path.expanduser().write_text(value, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select relevant passages from local text files without a model call."
    )
    request = parser.add_mutually_exclusive_group(required=True)
    request.add_argument("--request", help="Current user request.")
    request.add_argument(
        "--request-file",
        type=Path,
        help="UTF-8 file containing the current user request.",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        type=Path,
        help="Source file or directory. Repeat for more than one source.",
    )
    parser.add_argument(
        "--state",
        action="append",
        default=[],
        type=Path,
        help="Small accepted-state file. Repeat for more than one file.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        help=(
            "Directory to search when no --source or --state is supplied. "
            "Defaults to the current directory."
        ),
    )
    parser.add_argument("--max-packet-bytes", type=int, default=16_000)
    parser.add_argument("--max-file-bytes", type=int, default=4_000_000)
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the packet here. Omit to write it to standard output.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Write selection counts and byte totals as JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        request = read_required_text(
            direct=args.request,
            file_path=args.request_file,
            label="request",
        )
        root = find_ringer_root()
        sys.path.insert(0, str(root))
        from context_packet import build_context_packet

        automatic_source_search = not args.source and not args.state
        sources = args.source
        if automatic_source_search:
            sources = [
                args.root.expanduser() if args.root is not None else Path.cwd()
            ]
        packet = build_context_packet(
            request,
            sources=sources,
            state_files=args.state,
            max_packet_bytes=args.max_packet_bytes,
            max_file_bytes=args.max_file_bytes,
            max_files=args.max_files,
        )
        write_text(args.output, packet.text)
        report = packet.report()
        report["automatic_source_search"] = automatic_source_search
        report["omitted_source_bytes"] = max(
            0,
            int(report["source_bytes"]) - int(report["selected_source_bytes"]),
        )
        if args.report is not None:
            args.report.expanduser().write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        sys.stderr.write(
            "Selected "
            f"{len(packet.selected)} passage(s): "
            f"{report['selected_source_bytes']:,} of {report['source_bytes']:,} "
            f"source bytes; packet is {packet.packet_bytes:,} bytes.\n"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"select_context.py: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
