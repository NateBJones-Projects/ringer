#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


RESEARCH_HEADINGS = ("Summary", "Answer", "Evidence", "Uncertain", "Assumptions")
PROOF_HEADINGS = ("Claim", "How To Run", "What It Proves", "Limits")
MAX_RESEARCH_WORDS = 1600
MAX_PROOF_WORDS = 900
OPEN_PLACEHOLDER = "{" * 2
CLOSE_PLACEHOLDER = "}" * 2


def fail(name: str, detail: str) -> str:
    return f"FAIL [{name}]: {detail}"


def has_placeholder(value: str) -> bool:
    return OPEN_PLACEHOLDER in value or CLOSE_PLACEHOLDER in value


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def output_tail(text: str, limit: int = 4000) -> str:
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
                "research-with-proof validator: no POSIX shell found for the user command on native Windows; "
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


def validate_research(args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    report = Path(args.report)
    if not report.is_file():
        return [fail("missing_report", f"{report} does not exist")]
    if report.stat().st_size == 0:
        return [fail("empty_report", f"{report} is empty")]
    text = report.read_text(encoding="utf-8", errors="replace")
    if word_count(text) > MAX_RESEARCH_WORDS:
        failures.append(fail("too_long", f"report exceeds {MAX_RESEARCH_WORDS} words"))
    if not re.search(r"^#\s+Research Report\s*$", text, re.IGNORECASE | re.MULTILINE):
        failures.append(fail("missing_title", "report.md must start with '# Research Report'"))
    for heading in RESEARCH_HEADINGS:
        if not has_heading(text, heading):
            failures.append(fail("missing_section", f"report.md missing '## {heading}'"))
    if not re.search(r"https?://", text):
        failures.append(fail("missing_urls", "report.md must cite source URLs"))
    if not re.search(r"\bAccessed:\s*\d{4}-\d{2}-\d{2}\b", text, re.IGNORECASE):
        failures.append(fail("missing_access_dates", "Evidence items must include Accessed: YYYY-MM-DD"))
    for field in ("Claim:", "Source:", "Quoted Evidence:"):
        if field.lower() not in text.lower():
            failures.append(fail("missing_evidence_field", f"Evidence must include '{field}'"))
    if has_placeholder(str(args.topic)):
        failures.append(fail("placeholder_unfilled", "topic placeholder was not filled in the check command"))
    return failures


def validate_proof(args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    proof_doc = Path(args.proof_doc)
    artifact = Path(args.artifact)
    for name, value in (
        ("artifact", str(artifact)),
        ("proof_command", args.proof_command),
        ("success_marker", args.success_marker),
    ):
        if has_placeholder(value):
            failures.append(fail("placeholder_unfilled", f"{name} still contains an unfilled placeholder"))
    if not proof_doc.is_file():
        return failures + [fail("missing_proof_doc", f"{proof_doc} does not exist")]
    if proof_doc.stat().st_size == 0:
        return failures + [fail("empty_proof_doc", f"{proof_doc} is empty")]
    if not artifact.is_file():
        failures.append(fail("missing_artifact", f"{artifact} does not exist"))
    elif artifact.stat().st_size == 0:
        failures.append(fail("empty_artifact", f"{artifact} is empty"))

    text = proof_doc.read_text(encoding="utf-8", errors="replace")
    if word_count(text) > MAX_PROOF_WORDS:
        failures.append(fail("proof_doc_too_long", f"proof.md exceeds {MAX_PROOF_WORDS} words"))
    if not re.search(r"^#\s+Executable Proof\s*$", text, re.IGNORECASE | re.MULTILINE):
        failures.append(fail("missing_title", "proof.md must start with '# Executable Proof'"))
    for heading in PROOF_HEADINGS:
        if not has_heading(text, heading):
            failures.append(fail("missing_section", f"proof.md missing '## {heading}'"))

    if not has_placeholder(args.proof_command):
        result = run_user_command(args.proof_command)
        if result.returncode != 0:
            failures.append(
                fail(
                    "proof_command_failed",
                    f"command exited {result.returncode}: {args.proof_command}\n{output_tail(result.stdout)}",
                )
            )
        elif args.success_marker not in result.stdout:
            failures.append(fail("missing_success_marker", f"proof output did not contain {args.success_marker!r}"))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate research-with-proof outputs.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    research = subparsers.add_parser("research")
    research.add_argument("--report", required=True)
    research.add_argument("--topic", required=True)

    proof = subparsers.add_parser("proof")
    proof.add_argument("--proof-doc", required=True)
    proof.add_argument("--artifact", required=True)
    proof.add_argument("--proof-command", required=True)
    proof.add_argument("--success-marker", required=True)

    args = parser.parse_args()
    failures = validate_research(args) if args.mode == "research" else validate_proof(args)
    if failures:
        for item in failures:
            print(item)
        return 1
    print(f"PASS [{args.mode}_contract]: output contract is present and executable checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
