#!/usr/bin/env python3
"""Keep one accepted result and pair it with only the next requested change."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Sequence


STATE_VERSION = 1


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_text(*, direct: str | None, file_path: Path | None, label: str) -> str:
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


def save_state(path: Path, accepted_result: str) -> dict[str, object]:
    state = {
        "accepted_result": accepted_result,
        "accepted_result_bytes": len(accepted_result.encode("utf-8")),
        "accepted_result_sha256": sha256_text(accepted_result),
        "version": STATE_VERSION,
    }
    expanded = path.expanduser()
    expanded.parent.mkdir(parents=True, exist_ok=True)
    expanded.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return state


def load_state(path: Path) -> dict[str, object]:
    raw = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        raise ValueError("state file has an unsupported format")
    accepted = raw.get("accepted_result")
    if not isinstance(accepted, str) or not accepted.strip():
        raise ValueError("state file does not contain a non-empty accepted result")
    expected_hash = raw.get("accepted_result_sha256")
    if expected_hash != sha256_text(accepted):
        raise ValueError("accepted result hash does not match the state file")
    return raw


def make_packet(state: dict[str, object], change: str, max_packet_bytes: int) -> str:
    if max_packet_bytes < 1_024:
        raise ValueError("max-packet-bytes must be at least 1024")
    payload = json.dumps(
        {
            "accepted_result": state["accepted_result"],
            "new_change": change,
        },
        ensure_ascii=False,
    )
    packet = (
        "Continue from the accepted result. Apply only the new change. "
        "Do not reconstruct or summarize the earlier conversation. "
        "Return the updated result only.\n\n"
        "ACCEPTED_RESULT_AND_NEW_CHANGE_JSON\n"
        f"{payload}\n"
    )
    packet_bytes = len(packet.encode("utf-8"))
    if packet_bytes > max_packet_bytes:
        raise ValueError(
            f"packet is {packet_bytes:,} bytes, above the "
            f"{max_packet_bytes:,}-byte limit; select relevant passages from "
            "the accepted result before calling a model"
        )
    return packet


def add_text_input(
    parser: argparse.ArgumentParser,
    *,
    direct_flag: str,
    file_flag: str,
    direct_help: str,
    file_help: str,
) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(direct_flag, help=direct_help)
    group.add_argument(file_flag, type=Path, help=file_help)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replace chat history with one accepted result and one new change."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    save = commands.add_parser("save", help="Replace the saved accepted result.")
    save.add_argument("--state", required=True, type=Path)
    add_text_input(
        save,
        direct_flag="--accepted",
        file_flag="--accepted-file",
        direct_help="Accepted result text.",
        file_help="UTF-8 file containing the accepted result.",
    )

    packet = commands.add_parser(
        "packet",
        help="Build a prompt from the accepted result and the current change.",
    )
    packet.add_argument("--state", required=True, type=Path)
    add_text_input(
        packet,
        direct_flag="--change",
        file_flag="--change-file",
        direct_help="The user's new requested change.",
        file_help="UTF-8 file containing the user's new requested change.",
    )
    packet.add_argument("--max-packet-bytes", type=int, default=16_000)
    packet.add_argument(
        "--output",
        type=Path,
        help="Write the packet here. Omit to write it to standard output.",
    )

    inspect = commands.add_parser(
        "inspect",
        help="Print byte count and hash without printing the accepted result.",
    )
    inspect.add_argument("--state", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "save":
            accepted = read_text(
                direct=args.accepted,
                file_path=args.accepted_file,
                label="accepted result",
            )
            state = save_state(args.state, accepted)
            sys.stdout.write(
                json.dumps(
                    {
                        "accepted_result_bytes": state["accepted_result_bytes"],
                        "accepted_result_sha256": state["accepted_result_sha256"],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            return 0

        state = load_state(args.state)
        if args.command == "inspect":
            sys.stdout.write(
                json.dumps(
                    {
                        "accepted_result_bytes": state["accepted_result_bytes"],
                        "accepted_result_sha256": state["accepted_result_sha256"],
                        "version": state["version"],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            return 0

        change = read_text(
            direct=args.change,
            file_path=args.change_file,
            label="new change",
        )
        packet = make_packet(state, change, args.max_packet_bytes)
        if args.output is None:
            sys.stdout.write(packet)
        else:
            args.output.expanduser().write_text(packet, encoding="utf-8")
        return 0
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        parser.exit(2, f"state_delta.py: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
