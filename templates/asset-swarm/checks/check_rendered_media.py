#!/usr/bin/env python3
"""Run a host-side render command and validate the produced media files."""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import shlex
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
                "asset-swarm validator: no POSIX shell found for the user command on native Windows; "
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--render-command", required=True)
    parser.add_argument("--outputs", required=True, help="Comma-separated output files to validate")
    parser.add_argument("--min-bytes", type=int, default=100_000)
    args = parser.parse_args()

    fails: list[str] = []
    source = pathlib.Path(args.source)
    if not source.exists():
        print(f"FAIL: source file missing before render: {source}")
        return 1
    if source.stat().st_size == 0:
        print(f"FAIL: source file is empty before render: {source}")
        return 1

    print(f"running render command: {args.render_command}")
    proc = run_user_command(args.render_command, timeout=1800)
    if proc.returncode != 0:
        print("FAIL: render command failed")
        print(proc.stdout[-3000:])
        print(proc.stderr[-3000:])
        return 1
    if proc.stdout.strip():
        print(proc.stdout[-2000:])
    if proc.stderr.strip():
        print(proc.stderr[-2000:])

    for raw in [item.strip() for item in args.outputs.split(",") if item.strip()]:
        path = pathlib.Path(raw)
        if not path.exists():
            fails.append(f"missing rendered output: {path}")
            continue
        size = path.stat().st_size
        if size < args.min_bytes:
            fails.append(f"{path} is {size} bytes (need >= {args.min_bytes})")

    if fails:
        print("FAIL:")
        for fail in fails:
            print(f" - {fail}")
        print("render command tokens:", shlex.split(args.render_command)[:6])
        return 1
    print(f"PASS: rendered outputs exist and meet byte floor: {args.outputs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
