#!/usr/bin/env python3
"""Validate a sandboxed repo edit with build/tests, content checks, and git status allowlist."""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
from pathlib import Path


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


def run_user_command(command: str, *, cwd: Path | None = None, timeout: int | None = None, merge_stderr: bool = False) -> subprocess.CompletedProcess[str]:
    if sys.platform == "win32":
        shell_path = _windows_posix_shell()
        if shell_path is None:
            return subprocess.CompletedProcess(
                command,
                127,
                "repo-feature validator: no POSIX shell found for the user command on native Windows; "
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


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def path_allowed(path: str, allowed: list[str]) -> bool:
    normalized = path.strip().rstrip("/")
    for raw in allowed:
        candidate = raw.strip().rstrip("/")
        if not candidate:
            continue
        if normalized == candidate or normalized.startswith(candidate + "/"):
            return True
        if candidate.startswith(normalized + "/"):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--owned", required=True, help="Comma-separated repo paths the worker may change")
    parser.add_argument("--allowed-status", default="", help="Additional comma-separated paths allowed in git status")
    parser.add_argument("--required-paths", default="", help="Comma-separated repo paths that must exist")
    parser.add_argument("--required-text", default="", help="Comma-separated text snippets that must appear somewhere in owned files")
    parser.add_argument("--build-command", required=True)
    parser.add_argument("--notes", default="notes.md")
    args = parser.parse_args()

    repo = pathlib.Path(args.repo)
    fails: list[str] = []
    if not repo.exists():
        print(f"FAIL: repo path does not exist: {repo}")
        return 1
    if not (repo / ".git").exists():
        print(f"FAIL: repo path is not a git checkout: {repo}")
        return 1

    notes = pathlib.Path(args.notes)
    if not notes.exists() or notes.stat().st_size == 0:
        fails.append(f"scratch notes file missing or empty: {notes}")

    required_paths = split_csv(args.required_paths)
    for rel in required_paths:
        if not (repo / rel).exists():
            fails.append(f"required repo path missing: {rel}")

    owned = split_csv(args.owned)
    allowed = owned + split_csv(args.allowed_status)
    if not owned:
        fails.append("no owned paths supplied to validator")

    required_text = split_csv(args.required_text)
    if required_text:
        haystack_parts: list[str] = []
        for rel in owned:
            path = repo / rel
            if path.is_file():
                haystack_parts.append(path.read_text(encoding="utf-8", errors="replace"))
            elif path.is_dir():
                for child in path.rglob("*"):
                    if child.is_file() and child.stat().st_size < 1_000_000:
                        haystack_parts.append(child.read_text(encoding="utf-8", errors="replace"))
        haystack = "\n".join(haystack_parts)
        for snippet in required_text:
            if snippet not in haystack:
                fails.append(f"required text not found in owned files: {snippet!r}")

    print(f"running build/test command in {repo}: {args.build_command}")
    proc = run_user_command(args.build_command, cwd=repo, timeout=1800)
    if proc.returncode != 0:
        print("FAIL: build/test command failed")
        print(proc.stdout[-4000:])
        print(proc.stderr[-3000:])
        return 1
    print(proc.stdout[-2000:])
    if proc.stderr.strip():
        print(proc.stderr[-1000:])

    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, timeout=60)
    if status.returncode != 0:
        print("FAIL: git status failed")
        print(status.stderr)
        return 1
    for line in status.stdout.splitlines():
        if not line:
            continue
        rel = line[3:].strip()
        if not path_allowed(rel, allowed):
            fails.append(f"unexpected repo change outside owned/allowed paths: {line}")

    if fails:
        print("FAIL:")
        for fail in fails:
            print(f" - {fail}")
        return 1
    print("PASS: notes exist, content assertions passed, build/tests passed, and git status is allowlisted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
