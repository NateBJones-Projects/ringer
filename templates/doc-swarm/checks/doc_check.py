#!/usr/bin/env python3
"""Validate a doc-swarm output file."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".next",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".venv",
    "venv",
}


def split_list(value: str) -> list[str]:
    raw = value.strip()
    if not raw or raw.upper() == "NONE":
        return []
    parts: list[str] = []
    for chunk in raw.replace("\n", ";").replace(",", ";").split(";"):
        item = chunk.strip().strip("'\"")
        if item:
            parts.append(item)
    return parts


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_'-]+", text)


def heading_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def sections(markdown: str) -> dict[str, str]:
    result: dict[str, list[str]] = {}
    current = ""
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            current = heading_slug(match.group(2))
            result.setdefault(current, [])
            continue
        if current:
            result.setdefault(current, []).append(line)
    return {key: "\n".join(value).strip() for key, value in result.items()}


def extract_symbol_section(markdown: str, section_name: str) -> str:
    target = heading_slug(section_name)
    in_section = False
    collected: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            slug = heading_slug(match.group(2))
            if in_section and slug != target:
                break
            in_section = slug == target
            continue
        if in_section:
            collected.append(line)
    return "\n".join(collected)


def code_spans(text: str) -> list[str]:
    spans = re.findall(r"`([^`\n]+)`", text)
    cleaned: list[str] = []
    for span in spans:
        item = span.strip()
        if item and not item.startswith(("http://", "https://")):
            cleaned.append(item)
    return sorted(set(cleaned))


def source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.stat().st_size > 1_000_000:
            continue
        files.append(path)
    return files


def symbol_exists(symbol: str, files: list[Path]) -> bool:
    needle = symbol.encode("utf-8", errors="ignore")
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:2048]:
            continue
        if needle in data:
            return True
    return False


def fenced_blocks(markdown: str, language: str) -> list[str]:
    pattern = re.compile(r"```([^\n`]*)\n(.*?)\n```", re.DOTALL)
    blocks: list[str] = []
    wanted = language.strip().lower()
    for match in pattern.finditer(markdown):
        info = match.group(1).strip().split()[0].lower() if match.group(1).strip() else ""
        if info == wanted:
            blocks.append(match.group(2))
    return blocks


def suffix_for(language: str) -> str:
    mapping = {
        "python": ".py",
        "py": ".py",
        "bash": ".sh",
        "sh": ".sh",
        "javascript": ".js",
        "js": ".js",
        "typescript": ".ts",
        "ts": ".ts",
    }
    return mapping.get(language.lower(), ".txt")


def _windows_posix_shell() -> str | None:
    # Mirror ringer.py's check-shell resolution so verify commands keep the
    # same POSIX-shell contract inside the validator as outside it.
    env_shell = os.environ.get("RINGER_CHECK_SHELL")
    if env_shell:
        return env_shell
    bash = shutil.which("bash")
    if bash:
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
        system32 = os.path.normcase(os.path.abspath(os.path.join(system_root, "System32")))
        # System32 bash.exe launches WSL, whose Linux environment cannot run
        # commands that reference Windows paths — reject it.
        if not os.path.normcase(os.path.abspath(bash)).startswith(system32):
            return bash
    for candidate in (
        "C:/Program Files/Git/bin/bash.exe",
        "C:/Program Files/Git/usr/bin/bash.exe",
        "C:/Program Files (x86)/Git/bin/bash.exe",
        "C:/Program Files/Git/bin/sh.exe",
        "C:/Program Files/Git/usr/bin/sh.exe",
        "C:/Program Files (x86)/Git/bin/sh.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def run_user_command(command: str, *, cwd: Path | None = None, timeout: int | None = None, merge_stderr: bool = True) -> subprocess.CompletedProcess[str]:
    if sys.platform == "win32":
        shell_path = _windows_posix_shell()
        if shell_path is None:
            return subprocess.CompletedProcess(
                command,
                127,
                "doc-swarm validator: no POSIX shell found for the user command on native Windows; "
                "install Git for Windows or set RINGER_CHECK_SHELL.",
                None if merge_stderr else "",
            )
        return subprocess.run(
            [shell_path, "-c", command],
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            timeout=timeout,
        )
    return subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        timeout=timeout,
    )


def run_examples(blocks: list[str], language: str, runner: str, cwd: Path) -> bool:
    ok = True
    with tempfile.TemporaryDirectory(prefix="doc-example-") as tmp:
        tmpdir = Path(tmp)
        for index, block in enumerate(blocks, start=1):
            example = tmpdir / f"example_{index}{suffix_for(language)}"
            example.write_text(block + "\n", encoding="utf-8")
            command = runner.replace("{file}", str(example)).replace("{example_cwd}", str(cwd))
            proc = run_user_command(command, cwd=cwd, timeout=60)
            if proc.returncode != 0:
                print(f"FAIL: runnable example {index} failed with exit {proc.returncode}")
                print(proc.stdout[-2000:])
                ok = False
            else:
                print(f"OK: runnable example {index} executed")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc-path", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--required-sections", required=True)
    parser.add_argument("--min-words", required=True)
    parser.add_argument("--min-section-words", required=True)
    parser.add_argument("--symbol-section", required=True)
    parser.add_argument("--symbol-allowlist", default="NONE")
    parser.add_argument("--runnable-language", required=True)
    parser.add_argument("--example-runner", required=True)
    parser.add_argument("--example-cwd", required=True)
    args = parser.parse_args()

    doc_path = Path(args.doc_path)
    source_root = Path(args.source_root)
    example_cwd = Path(args.example_cwd)
    try:
        min_words = int(args.min_words)
        min_section_words = int(args.min_section_words)
    except ValueError:
        print("FAIL: MIN_WORDS and MIN_SECTION_WORDS must be integers")
        return 1

    if not doc_path.exists() or doc_path.stat().st_size == 0:
        print(f"FAIL: doc file is missing or empty: {doc_path}")
        return 1
    if not source_root.exists():
        print(f"FAIL: source root does not exist: {source_root}")
        return 1
    if not example_cwd.exists():
        print(f"FAIL: example cwd does not exist: {example_cwd}")
        return 1

    markdown = doc_path.read_text(encoding="utf-8")
    all_words = words(markdown)
    if len(all_words) < min_words:
        print(f"FAIL: doc has {len(all_words)} words; minimum is {min_words}")
        return 1

    found_sections = sections(markdown)
    failures = False
    for section in split_list(args.required_sections):
        slug = heading_slug(section)
        body = found_sections.get(slug, "")
        if slug not in found_sections:
            print(f"FAIL: missing required section: {section}")
            failures = True
            continue
        count = len(words(body))
        if count < min_section_words:
            print(f"FAIL: section '{section}' has {count} words; minimum is {min_section_words}")
            failures = True

    symbol_body = extract_symbol_section(markdown, args.symbol_section)
    symbols = code_spans(symbol_body)
    allowlist = set(split_list(args.symbol_allowlist))
    symbols_to_check = [symbol for symbol in symbols if symbol not in allowlist]
    if not symbols_to_check:
        print(f"FAIL: no checkable code-span symbols found in section '{args.symbol_section}'")
        failures = True
    else:
        files = source_files(source_root)
        for symbol in symbols_to_check:
            if not symbol_exists(symbol, files):
                print(f"FAIL: documented symbol not found in source tree: {symbol}")
                failures = True

    examples = fenced_blocks(markdown, args.runnable_language)
    if not examples:
        print(f"FAIL: no runnable fenced examples labeled {args.runnable_language}")
        failures = True
    elif not run_examples(examples, args.runnable_language, args.example_runner, example_cwd):
        failures = True

    if failures:
        return 1
    print(f"OK: {doc_path} passed doc validation")
    print(f"OK: {len(symbols_to_check)} documented symbols found in source")
    print(f"OK: {len(examples)} runnable examples executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

