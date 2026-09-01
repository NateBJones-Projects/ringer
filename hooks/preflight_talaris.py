#!/usr/bin/env python3
"""
Cross-host evidence preflight collector for Ringer.

Collects Talaris-side evidence via SSH BEFORE the Ringer worker enters the
Seatbelt sandbox. Stages results locally so the sandboxed judge can read
pinned local files instead of needing network access.

Usage:
    python3 preflight_talaris.py <taskdir> <manifest_preflight_json>

The manifest preflight JSON should look like:
{
    "ssh_host": "talaris",
    "commands": [
        "git -C /Users/jack.reis/Projects/wt-deploy rev-parse HEAD",
        "git -C /Users/jack.reis/Projects/wt-deploy status --porcelain",
        "lsof -nP -iTCP:8082 -sTCP:LISTEN"
    ],
    "files_to_fetch": [
        "/Users/jack.reis/Projects/wt-deploy/package.json"
    ],
    "stage_dir": "preflight/"
}

The script:
1. SSHes to the host (outside Seatbelt, full network)
2. Runs each command, captures stdout/stderr/exit_code
3. Fetches any files_to_fetch via scp
4. Writes everything to {taskdir}/{stage_dir}/
5. Generates SHA256SUMS for all staged files
6. Writes a manifest.json describing what was collected

The sandboxed check script then reads pinned local files + verifies checksums.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def ssh_command(host: str, command: str, timeout: int = 120) -> dict:
    """Run a command on a remote host via SSH. Returns result dict."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "host": host,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
            "host": host,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "host": host,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }


def scp_file(host: str, remote_path: str, local_path: Path, timeout: int = 30) -> dict:
    """Fetch a file from a remote host via SCP. Returns result dict."""
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             f"{host}:{remote_path}", str(local_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "remote_path": remote_path,
            "local_path": str(local_path),
            "exit_code": result.returncode,
            "stderr": result.stderr,
            "bytes": local_path.stat().st_size if local_path.exists() else 0,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {
            "remote_path": remote_path,
            "local_path": str(local_path),
            "exit_code": -1,
            "stderr": str(e),
            "bytes": 0,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }


def sha256_file(path: Path) -> str:
    """Calculate SHA-256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_preflight(taskdir: Path, preflight_config: dict) -> dict:
    """Run the full preflight collection sequence."""
    ssh_host = preflight_config.get("ssh_host", "talaris")
    commands = preflight_config.get("commands", [])
    files_to_fetch = preflight_config.get("files_to_fetch", [])
    stage_dir_name = preflight_config.get("stage_dir", "preflight/")
    stage_dir = taskdir / stage_dir_name
    stage_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "host": ssh_host,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "commands": [],
        "files": [],
        "stage_dir": str(stage_dir),
    }

    # Run SSH commands
    for cmd in commands:
        print(f"[preflight] ssh {ssh_host}: {cmd[:80]}...", file=sys.stderr)
        result = ssh_command(ssh_host, cmd)
        # Write command output to a file named by hash of the command
        cmd_hash = hashlib.sha256(cmd.encode()).hexdigest()[:16]
        out_path = stage_dir / f"cmd_{cmd_hash}.out"
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        results["commands"].append({
            "command": cmd,
            "output_file": f"cmd_{cmd_hash}.out",
            "exit_code": result["exit_code"],
            "stdout_len": len(result["stdout"]),
            "stderr_len": len(result["stderr"]),
        })

    # Fetch files via SCP
    for remote_path in files_to_fetch:
        print(f"[preflight] scp {ssh_host}:{remote_path}", file=sys.stderr)
        local_name = Path(remote_path).name
        local_path = stage_dir / f"file_{local_name}"
        result = scp_file(ssh_host, remote_path, local_path)
        results["files"].append(result)

    # Generate SHA256SUMS for all files in stage_dir
    sha256sums_path = stage_dir / "SHA256SUMS"
    sha256_lines = []
    for f in sorted(stage_dir.iterdir()):
        if f.is_file() and f.name != "SHA256SUMS":
            digest = sha256_file(f)
            sha256_lines.append(f"{digest}  {f.name}")
    sha256sums_path.write_text("\n".join(sha256_lines) + "\n")

    # Write manifest
    manifest_path = stage_dir / "manifest.json"
    manifest_path.write_text(json.dumps(results, indent=2) + "\n")

    # Verify checksums
    verify = subprocess.run(
        ["shasum", "-a", "256", "-c", str(sha256sums_path)],
        cwd=str(stage_dir),
        capture_output=True,
        text=True,
    )
    results["sha256sums_verify"] = {
        "exit_code": verify.returncode,
        "stdout": verify.stdout,
    }

    print(f"[preflight] Staged {len(results['commands'])} command outputs + "
          f"{len(results['files'])} files to {stage_dir}", file=sys.stderr)
    print(f"[preflight] SHA256SUMS: {sha256sums_path}", file=sys.stderr)

    return results


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: preflight_talaris.py <taskdir> <manifest_preflight_json>", file=sys.stderr)
        print("", file=sys.stderr)
        print("The manifest_preflight_json should be a JSON object with:", file=sys.stderr)
        print('  {"ssh_host": "talaris", "commands": [...], "files_to_fetch": [...], "stage_dir": "preflight/"}', file=sys.stderr)
        return 1

    taskdir = Path(sys.argv[1]).expanduser().resolve()
    if not taskdir.is_dir():
        print(f"Task directory not found: {taskdir}", file=sys.stderr)
        return 1

    preflight_config_path = Path(sys.argv[2]).expanduser().resolve()
    if not preflight_config_path.is_file():
        # Try parsing as inline JSON
        try:
            preflight_config = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            print(f"Preflight config not found and not valid JSON: {sys.argv[2]}", file=sys.stderr)
            return 1
    else:
        preflight_config = json.loads(preflight_config_path.read_text())

    results = run_preflight(taskdir, preflight_config)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())