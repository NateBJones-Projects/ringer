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


REQUIRED_HEADINGS = ("Criteria Grades", "Evidence", "Failures Or Costs")
MAX_WORDS = 1000
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
                "bakeoff validator: no POSIX shell found for the user command on native Windows; "
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


def has_heading(text: str, heading: str) -> bool:
    return bool(re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.IGNORECASE | re.MULTILINE))


def run_validator(command: str, session_dir: Path, expected_model: str) -> list[str]:
    if has_placeholder(command):
        return [fail("validator_unfilled", "session-validator still contains an unfilled placeholder")]
    if not command.strip() or command.strip().lower() == "none":
        return [fail("validator_missing", "session-validator must prove the expected model actually ran")]
    command_to_run = command.replace("{session_dir}", shlex.quote(str(session_dir))).replace(
        "{expected_model}", shlex.quote(expected_model)
    )
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
    parser = argparse.ArgumentParser(description="Validate one bakeoff matrix cell.")
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--notes", required=True, type=Path)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--session-validator", required=True)
    args = parser.parse_args()

    failures: list[str] = []
    for name, value in (
        ("session_dir", str(args.session_dir)),
        ("notes", str(args.notes)),
        ("expected_model", args.expected_model),
    ):
        if has_placeholder(value):
            failures.append(fail("placeholder_unfilled", f"{name} still contains an unfilled placeholder"))

    if not args.session_dir.is_dir():
        failures.append(fail("missing_session_dir", f"{args.session_dir} does not exist"))
    if not args.notes.is_file():
        failures.append(fail("missing_notes", f"{args.notes} does not exist"))
        text = ""
    elif args.notes.stat().st_size == 0:
        failures.append(fail("empty_notes", f"{args.notes} is empty"))
        text = ""
    else:
        text = args.notes.read_text(encoding="utf-8", errors="replace")

    if text:
        if word_count(text) > MAX_WORDS:
            failures.append(fail("too_long", f"evaluator notes exceed {MAX_WORDS} words"))
        if not re.search(r"^#\s+Bakeoff Cell\s*$", text, re.IGNORECASE | re.MULTILINE):
            failures.append(fail("missing_title", "evaluator-notes.md must start with '# Bakeoff Cell'"))
        if args.expected_model not in text:
            failures.append(fail("model_not_named", "evaluator-notes.md must name the expected model"))
        for heading in REQUIRED_HEADINGS:
            if not has_heading(text, heading):
                failures.append(fail("missing_section", f"evaluator-notes.md missing '## {heading}'"))
        if not re.search(r"^VERDICT:\s*\S", text, re.IGNORECASE | re.MULTILINE):
            failures.append(fail("missing_verdict", "evaluator-notes.md must end with a VERDICT line"))
        if not re.search(r"\b(PASS|FAIL|MIXED)\b", text, re.IGNORECASE):
            failures.append(fail("missing_grade", "criteria must be graded with PASS, FAIL, or MIXED"))

    failures.extend(run_validator(args.session_validator, args.session_dir, args.expected_model))

    if failures:
        for item in failures:
            print(item)
        return 1
    print(f"PASS [bakeoff_contract]: {args.expected_model} produced graded notes with a validated session")
    return 0


if __name__ == "__main__":
    sys.exit(main())
