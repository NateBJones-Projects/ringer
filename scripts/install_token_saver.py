#!/usr/bin/env python3
"""Install the standalone token-saver skill for Codex and Claude Code."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Sequence


PACKAGE_FILES = (
    Path("SKILL.md"),
    Path("agents/openai.yaml"),
    Path("scripts/context_packet.py"),
    Path("scripts/select_context.py"),
    Path("scripts/state_delta.py"),
)
IGNORED_NAMES = {".DS_Store", "__pycache__"}


def package_source() -> Path:
    source = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "token-saver"
    missing = [str(relative) for relative in PACKAGE_FILES if not (source / relative).is_file()]
    if missing:
        raise RuntimeError("installer package is incomplete: " + ", ".join(missing))
    return source


def visible_files(root: Path) -> set[Path]:
    files: set[Path] = set()
    for path in root.rglob("*"):
        if any(part in IGNORED_NAMES for part in path.relative_to(root).parts):
            continue
        if path.is_file() and path.suffix != ".pyc":
            files.add(path.relative_to(root))
    return files


def packages_match(source: Path, target: Path) -> bool:
    if target.is_symlink() or not target.is_dir():
        return False
    expected = set(PACKAGE_FILES)
    if visible_files(target) != expected:
        return False
    return all(
        (source / relative).read_bytes() == (target / relative).read_bytes()
        for relative in PACKAGE_FILES
    )


def copy_package(source: Path, destination: Path) -> None:
    for relative in PACKAGE_FILES:
        target_file = destination / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target_file)


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def replace_package(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    prepared = Path(
        tempfile.mkdtemp(prefix=".token-saver-install-", dir=str(target.parent))
    )
    backup: Path | None = None
    try:
        copy_package(source, prepared)
        if target.exists():
            backup = target.parent / f".token-saver-backup-{os.getpid()}"
            suffix = 0
            while backup.exists():
                suffix += 1
                backup = target.parent / f".token-saver-backup-{os.getpid()}-{suffix}"
            target.rename(backup)
        prepared.rename(target)
        if backup is not None:
            remove_path(backup)
    except Exception:
        if target.exists() and backup is not None and backup.exists():
            remove_path(target)
        if backup is not None and backup.exists() and not target.exists():
            backup.rename(target)
        raise
    finally:
        if prepared.exists():
            remove_path(prepared)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install the standalone token-saver skill for Codex and Claude Code. "
            "Ringer is not required."
        )
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace a different existing token-saver skill in both locations.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = package_source()
        home = args.home.expanduser().resolve()
        targets = (
            ("Codex", home / ".agents" / "skills" / "token-saver"),
            ("Claude Code", home / ".claude" / "skills" / "token-saver"),
        )

        conflicts = [
            (host, target)
            for host, target in targets
            if target.exists() and not packages_match(source, target)
        ]
        if conflicts and not args.force:
            lines = "\n".join(f"- {host}: {target}" for host, target in conflicts)
            sys.stderr.write(
                "A different token-saver skill already exists:\n"
                f"{lines}\n"
                "Nothing was changed. Review it first, or rerun with --force.\n"
            )
            return 2

        for host, target in targets:
            if packages_match(source, target):
                print(f"{host}: already current at {target}")
                continue
            replace_package(source, target)
            print(f"{host}: installed at {target}")
        return 0
    except (OSError, RuntimeError) as exc:
        sys.stderr.write(f"install_token_saver.py: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
