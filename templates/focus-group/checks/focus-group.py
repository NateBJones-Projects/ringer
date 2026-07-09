#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


REACTION_HEADINGS = ("What Landed", "What Felt Wrong", "Would I Continue")
NOTES_HEADINGS = ("Criteria Grades", "Evidence", "Assumptions")
MAX_TOTAL_WORDS = 1500
OPEN_PLACEHOLDER = "{" * 2
CLOSE_PLACEHOLDER = "}" * 2


def fail(name: str, detail: str) -> str:
    return f"FAIL [{name}]: {detail}"


def has_placeholder(value: str) -> bool:
    return OPEN_PLACEHOLDER in value or CLOSE_PLACEHOLDER in value


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def output_tail(text: str, limit: int = 3000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


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
                "focus-group validator: no POSIX shell found for the user command on native Windows; "
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


def read_required(path: Path, label: str) -> tuple[str, list[str]]:
    if not path.is_file():
        return "", [fail(f"missing_{label}", f"{path} does not exist")]
    if path.stat().st_size == 0:
        return "", [fail(f"empty_{label}", f"{path} is empty")]
    return path.read_text(encoding="utf-8", errors="replace"), []


def has_heading(text: str, heading: str) -> bool:
    return bool(re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.IGNORECASE | re.MULTILINE))


def run_validator(command: str, session_dir: Path) -> list[str]:
    if has_placeholder(command):
        return [fail("validator_unfilled", "session-validator still contains an unfilled placeholder")]
    if not command.strip() or command.strip().lower() == "none":
        return [fail("validator_missing", "session-validator must run a real transcript/product check")]
    command_to_run = command.replace("{session_dir}", shlex.quote(str(session_dir)))
    result = run_user_command(command_to_run)
    if result.returncode != 0:
        return [
            fail(
                "session_validator_failed",
                f"command exited {result.returncode}: {command_to_run}\n{output_tail(result.stdout)}",
            )
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a focus-group persona output contract.")
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--reaction", required=True, type=Path)
    parser.add_argument("--notes", required=True, type=Path)
    parser.add_argument("--session-validator", required=True)
    args = parser.parse_args()

    failures: list[str] = []
    for name, value in (
        ("session_dir", str(args.session_dir)),
        ("reaction", str(args.reaction)),
        ("notes", str(args.notes)),
    ):
        if has_placeholder(value):
            failures.append(fail("placeholder_unfilled", f"{name} still contains an unfilled placeholder"))

    if not args.session_dir.is_dir():
        failures.append(fail("missing_session_dir", f"{args.session_dir} does not exist"))

    reaction, reaction_failures = read_required(args.reaction, "reaction")
    notes, notes_failures = read_required(args.notes, "notes")
    failures.extend(reaction_failures)
    failures.extend(notes_failures)

    if reaction:
        if not re.search(r"^#\s+Persona Reaction\s*$", reaction, re.IGNORECASE | re.MULTILINE):
            failures.append(fail("reaction_missing_title", "reaction.md must start with '# Persona Reaction'"))
        for heading in REACTION_HEADINGS:
            if not has_heading(reaction, heading):
                failures.append(fail("reaction_missing_section", f"reaction.md missing '## {heading}'"))
    if notes:
        if not re.search(r"^#\s+Evaluator Notes\s*$", notes, re.IGNORECASE | re.MULTILINE):
            failures.append(fail("notes_missing_title", "evaluator-notes.md must start with '# Evaluator Notes'"))
        for heading in NOTES_HEADINGS:
            if not has_heading(notes, heading):
                failures.append(fail("notes_missing_section", f"evaluator-notes.md missing '## {heading}'"))
        if not re.search(r"\b(PASS|FAIL|MIXED)\b", notes, re.IGNORECASE):
            failures.append(fail("notes_missing_grade", "evaluator-notes.md must grade criteria with PASS, FAIL, or MIXED"))
        if "evidence" not in notes.lower():
            failures.append(fail("notes_missing_evidence", "evaluator-notes.md must cite session evidence"))

    if reaction and notes and word_count(reaction + "\n" + notes) > MAX_TOTAL_WORDS:
        failures.append(fail("too_long", f"reaction plus notes exceed {MAX_TOTAL_WORDS} words"))

    failures.extend(run_validator(args.session_validator, args.session_dir))

    if failures:
        for item in failures:
            print(item)
        return 1
    print(f"PASS [focus_group_contract]: {args.session_dir} has persona reaction, graded notes, and a validated session")
    return 0


if __name__ == "__main__":
    sys.exit(main())
