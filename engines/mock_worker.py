#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


class MockSpecError(ValueError):
    pass


def has_fail_directive(spec: str) -> bool:
    return any(line.strip() == "MOCK_FAIL" for line in spec.splitlines())


def has_engine_down_directive(spec: str) -> bool:
    return any(line.strip() == "MOCK_ENGINE_DOWN" for line in spec.splitlines())


def parse_blocks(spec: str) -> list[tuple[str, str]]:
    lines = spec.splitlines()
    blocks: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("MOCK_FILE: "):
            index += 1
            continue

        raw_path = line.removeprefix("MOCK_FILE: ").strip()
        if not raw_path:
            raise MockSpecError("empty MOCK_FILE path")

        index += 1
        content_lines: list[str] = []
        while index < len(lines) and lines[index].strip() != "MOCK_END":
            content_lines.append(lines[index])
            index += 1

        if index >= len(lines):
            raise MockSpecError(f"unterminated MOCK_FILE block: {raw_path}")

        content = "\n".join(content_lines)
        if content_lines:
            content += "\n"
        blocks.append((raw_path, content))
        index += 1

    return blocks


def resolve_output_path(raw_path: str, cwd: Path) -> Path:
    relative = Path(raw_path)
    if relative.is_absolute():
        raise MockSpecError(f"MOCK_FILE path must be relative: {raw_path}")

    root = cwd.resolve()
    target = (root / relative).resolve()
    if target == root or root not in target.parents:
        raise MockSpecError(f"MOCK_FILE path escapes task directory: {raw_path}")
    return target


def write_blocks(blocks: list[tuple[str, str]], cwd: Path) -> list[str]:
    written: list[str] = []
    for raw_path, content in blocks:
        target = resolve_output_path(raw_path, cwd)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(raw_path)
    return written


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("mock-worker: missing spec argument", file=sys.stderr)
        return 2

    spec = argv[-1]
    if has_engine_down_directive(spec):
        print("mock-worker: 402 Payment Required: usage balance exhausted", file=sys.stderr)
        return 1
    if has_fail_directive(spec):
        print("mock-worker: simulated failure")
        return 1

    try:
        blocks = parse_blocks(spec)
        if not blocks:
            print("mock-worker: no MOCK_FILE blocks found")
            return 0
        written = write_blocks(blocks, Path.cwd())
    except MockSpecError as exc:
        print(f"mock-worker: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"mock-worker: write failed: {exc}", file=sys.stderr)
        return 1

    print(f"mock-worker: wrote {len(written)} file(s): {', '.join(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
