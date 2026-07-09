#!/usr/bin/env python3
"""Validate a test-hardening worker output."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


AUTO_TEST_COUNT_PATTERNS = [
    r"Tests:\s+.*?(\d+)\s+passed",
    r"Test Files\s+.*?(\d+)\s+passed",
    r"(\d+)\s+passed(?:,|\s+in|\s*$)",
    r"collected\s+(\d+)\s+items?",
    r"Ran\s+(\d+)\s+tests?",
    r"(\d+)\s+passing",
]


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


def normalize(path: str) -> str:
    return path.strip().strip("/").replace("\\", "/")


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
                "test-hardening validator: no POSIX shell found for the user command on native Windows; "
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


def is_under(path: str, roots: list[str]) -> bool:
    rel = normalize(path)
    for root in roots:
        item = normalize(root)
        if not item:
            continue
        if item.endswith("/"):
            if rel.startswith(item):
                return True
        elif rel == item or rel.startswith(f"{item}/"):
            return True
    return False


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def changed_paths_from_status() -> list[str]:
    proc = run_git(["status", "--porcelain"])
    if proc.returncode != 0:
        print("FAIL: could not inspect git status")
        print(proc.stderr.strip())
        sys.exit(1)
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        payload = line[3:].strip()
        if " -> " in payload:
            payload = payload.split(" -> ", 1)[1].strip()
        paths.append(normalize(payload))
    return paths


def validate_changed_paths(changed: list[str], owned: list[str], forbidden: list[str]) -> bool:
    ok = True
    if not changed:
        print("FAIL: git status shows no changed files; no tests were added or edited")
        return False
    outside = [path for path in changed if not is_under(path, owned)]
    if outside:
        print("FAIL: changed paths outside owned test files:")
        for path in outside:
            print(f"  - {path}")
        ok = False
    blocked = [path for path in changed if is_under(path, forbidden)]
    if blocked:
        print("FAIL: changed paths under forbidden production paths:")
        for path in blocked:
            print(f"  - {path}")
        ok = False
    return ok


def validate_new_files(files: list[str]) -> bool:
    ok = True
    if not files:
        print("FAIL: NEW_TEST_FILES is empty; the check cannot prove new tests exist")
        return False
    for rel in files:
        path = Path(rel)
        if not path.exists() or path.stat().st_size == 0:
            print(f"FAIL: required new test file is missing or empty: {rel}")
            ok = False
    return ok


def assertion_stats(path: Path, pattern: re.Pattern[str]) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8")
    assertions = len(pattern.findall(text))
    substance_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("#", "//", "/*", "*")):
            continue
        substance_lines += 1
    return assertions, max(1, substance_lines)


def validate_assertions(files: list[str], assertion_pattern: str, min_per_file: int, min_density: float) -> bool:
    ok = True
    try:
        pattern = re.compile(assertion_pattern)
    except re.error as exc:
        print(f"FAIL: ASSERTION_PATTERN is not valid regex: {exc}")
        return False
    for rel in files:
        path = Path(rel)
        if not path.exists():
            continue
        assertions, lines = assertion_stats(path, pattern)
        density = assertions / lines
        if assertions < min_per_file:
            print(f"FAIL: {rel} has {assertions} assertion matches; minimum is {min_per_file}")
            ok = False
        if density < min_density:
            print(f"FAIL: {rel} assertion density is {density:.3f}; minimum is {min_density:.3f}")
            ok = False
    return ok


def run_tests(command: str) -> tuple[bool, str]:
    proc = run_user_command(command, timeout=1200)
    output = proc.stdout
    if proc.returncode != 0:
        print(f"FAIL: TEST_COMMAND exited {proc.returncode}")
        print(output[-4000:])
        return False, output
    print("OK: TEST_COMMAND passed")
    print(output[-2000:])
    return True, output


def parse_test_count(output: str, regex: str) -> int | None:
    patterns = AUTO_TEST_COUNT_PATTERNS if regex.strip().upper() == "AUTO" else [regex]
    matches: list[int] = []
    for pattern in patterns:
        try:
            compiled = re.compile(pattern, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        except re.error as exc:
            print(f"FAIL: TEST_COUNT_REGEX is not valid regex: {exc}")
            return None
        for match in compiled.finditer(output):
            try:
                matches.append(int(match.group(1)))
            except (IndexError, ValueError):
                continue
    return max(matches) if matches else None


def export_patch(patch_path: Path, owned: list[str], forbidden: list[str]) -> bool:
    add = run_git(["add", "-A"])
    if add.returncode != 0:
        print("FAIL: git add -A failed")
        print(add.stderr.strip())
        return False
    diff = run_git(["diff", "--cached", "--name-only", "--diff-filter=ACMRTD"])
    if diff.returncode != 0:
        print("FAIL: could not inspect staged diff")
        print(diff.stderr.strip())
        return False
    staged = [normalize(line) for line in diff.stdout.splitlines() if line.strip()]
    if not staged:
        print("FAIL: no staged paths after git add -A")
        return False
    if not validate_changed_paths(staged, owned, forbidden):
        return False
    patch = run_git(["diff", "--cached"])
    if patch.returncode != 0:
        print("FAIL: git diff --cached failed")
        print(patch.stderr.strip())
        return False
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(patch.stdout, encoding="utf-8")
    if not patch_path.exists() or patch_path.stat().st_size == 0:
        print(f"FAIL: exported patch is missing or empty: {patch_path}")
        return False
    print(f"OK: exported non-empty test patch {patch_path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-key", required=True)
    parser.add_argument("--test-command", required=True)
    parser.add_argument("--baseline-test-count", required=True)
    parser.add_argument("--test-count-regex", required=True)
    parser.add_argument("--new-test-files", required=True)
    parser.add_argument("--owned-test-files", required=True)
    parser.add_argument("--forbidden-paths", required=True)
    parser.add_argument("--assertion-pattern", required=True)
    parser.add_argument("--min-assertions-per-file", required=True)
    parser.add_argument("--min-assertion-density", required=True)
    parser.add_argument("--export-patch", required=True)
    args = parser.parse_args()

    try:
        baseline = int(args.baseline_test_count)
        min_assertions = int(args.min_assertions_per_file)
        min_density = float(args.min_assertion_density)
    except ValueError:
        print("FAIL: BASELINE_TEST_COUNT, MIN_ASSERTIONS_PER_FILE, and MIN_ASSERTION_DENSITY must be numeric")
        return 1

    owned = split_list(args.owned_test_files)
    forbidden = split_list(args.forbidden_paths)
    new_files = split_list(args.new_test_files)
    if not owned:
        print("FAIL: owned test file list is empty")
        return 1

    ok = True
    changed = changed_paths_from_status()
    ok = validate_changed_paths(changed, owned, forbidden) and ok
    ok = validate_new_files(new_files) and ok
    ok = validate_assertions(new_files, args.assertion_pattern, min_assertions, min_density) and ok

    tests_ok, output = run_tests(args.test_command)
    ok = tests_ok and ok
    count = parse_test_count(output, args.test_count_regex)
    if count is None:
        print("FAIL: could not parse test count from TEST_COMMAND output")
        ok = False
    elif count <= baseline:
        print(f"FAIL: test count did not increase; baseline={baseline}, parsed={count}")
        ok = False
    else:
        print(f"OK: test count increased from {baseline} to {count}")

    ok = export_patch(Path(args.export_patch), owned, forbidden) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

