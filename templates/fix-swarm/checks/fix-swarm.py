#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


MAX_SUMMARY_WORDS = 700
SUMMARY_HEADINGS = ("Summary", "Files Changed", "Verification", "Assumptions")
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


def run_shell(command: str) -> subprocess.CompletedProcess[str]:
    if sys.platform == "win32":
        shell_path = _windows_posix_shell()
        if shell_path is None:
            return subprocess.CompletedProcess(
                command,
                127,
                "fix-swarm validator: no POSIX shell found for the verify command on "
                "native Windows; install Git for Windows or set RINGER_CHECK_SHELL.",
                None,
            )
        return subprocess.run(
            [shell_path, "-c", command],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    return subprocess.run(
        command,
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def run_git_bytes(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    # Patch content must stay byte-faithful: decoding through the locale
    # (cp1252 on Windows) corrupts UTF-8 and newline translation breaks hunks.
    return subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def parse_owned_files(raw: str) -> list[str]:
    normalized = raw.replace("\\n", "\n").replace(";", "\n").replace(",", "\n")
    paths: list[str] = []
    for line in normalized.splitlines():
        item = line.strip().strip("'\"")
        if not item:
            continue
        if item.startswith("./"):
            item = item[2:]
        paths.append(item.rstrip("/"))
    return paths


def allowed(path: str, owned: list[str]) -> bool:
    if "*" in owned:
        return True
    clean = path[2:] if path.startswith("./") else path
    for item in owned:
        if clean == item or clean.startswith(item.rstrip("/") + "/"):
            return True
    return False


def validate_summary(summary_path: Path, exported_summary: Path) -> list[str]:
    failures: list[str] = []
    if not summary_path.is_file():
        return [fail("missing_summary", f"{summary_path} does not exist")]
    if summary_path.stat().st_size == 0:
        return [fail("empty_summary", f"{summary_path} is empty")]

    text = summary_path.read_text(encoding="utf-8", errors="replace")
    if word_count(text) > MAX_SUMMARY_WORDS:
        failures.append(fail("summary_too_long", f"summary has more than {MAX_SUMMARY_WORDS} words"))
    if not re.search(r"^#\s+Fix Summary\s*$", text, re.IGNORECASE | re.MULTILINE):
        failures.append(fail("missing_title", "fix-summary.md must start with '# Fix Summary'"))
    for heading in SUMMARY_HEADINGS:
        if not re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.IGNORECASE | re.MULTILINE):
            failures.append(fail("missing_summary_section", f"fix-summary.md missing '## {heading}'"))
    if not has_placeholder(str(exported_summary)):
        exported_summary.parent.mkdir(parents=True, exist_ok=True)
        exported_summary.write_text(text, encoding="utf-8")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fix verification and export a checked patch.")
    parser.add_argument("--verify-command", required=True)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--exported-summary", required=True, type=Path)
    parser.add_argument("--owned-files", required=True)
    args = parser.parse_args()

    failures: list[str] = []
    for name, value in (
        ("verify_command", args.verify_command),
        ("patch", str(args.patch)),
        ("exported_summary", str(args.exported_summary)),
        ("owned_files", args.owned_files),
    ):
        if has_placeholder(value):
            failures.append(fail("placeholder_unfilled", f"{name} still contains an unfilled placeholder"))

    owned_files = parse_owned_files(args.owned_files)
    if not owned_files:
        failures.append(fail("missing_owned_files", "owned-files list is empty"))

    failures.extend(validate_summary(args.summary, args.exported_summary))

    if not has_placeholder(args.verify_command):
        verify = run_shell(args.verify_command)
        if verify.returncode != 0:
            failures.append(
                fail(
                    "verify_command_failed",
                    f"command exited {verify.returncode}: {args.verify_command}\n{output_tail(verify.stdout)}",
                )
            )

    add_result = run_git(["add", "-A"])
    if add_result.returncode != 0:
        failures.append(fail("git_add_failed", output_tail(add_result.stdout)))

    if not args.summary.is_absolute() and args.summary.exists():
        run_git(["reset", "--quiet", "--", str(args.summary)])

    names_result = run_git_bytes(["diff", "--cached", "--name-only", "-z"])
    names_text = names_result.stdout.decode("utf-8", errors="replace")
    changed_files = [item for item in names_text.split("\0") if item]
    if names_result.returncode != 0:
        failures.append(fail("git_diff_names_failed", output_tail(names_text)))
    elif not changed_files:
        failures.append(fail("empty_patch", "no staged changes were produced"))
    else:
        for changed in changed_files:
            if not allowed(changed, owned_files):
                failures.append(fail("outside_owned_files", f"{changed} is not in the owned-files list"))

    patch_result = run_git_bytes(["diff", "--cached", "--binary"])
    if patch_result.returncode != 0:
        failures.append(fail("git_diff_failed", output_tail(patch_result.stdout.decode("utf-8", errors="replace"))))
    elif patch_result.stdout.strip() and not has_placeholder(str(args.patch)):
        args.patch.parent.mkdir(parents=True, exist_ok=True)
        args.patch.write_bytes(patch_result.stdout)

    if not has_placeholder(str(args.patch)) and (not args.patch.is_file() or args.patch.stat().st_size == 0):
        failures.append(fail("patch_not_written", f"{args.patch} was not written or is empty"))

    if failures:
        for item in failures:
            print(item)
        return 1
    print(f"PASS [fix_contract]: exported {args.patch} with {len(changed_files)} changed file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
