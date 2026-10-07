#!/usr/bin/env python3
"""Validate a sandboxed repo edit: harvest staged files, run build/tests, content checks, and a git status allowlist."""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys


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


def harvest_staged_files(stage_dir: str, repo: pathlib.Path, owned: list[str]) -> tuple[int, bool]:
    """Install files staged at <stage_dir>/<repo-rel> into the repo.

    Write-confined workers (e.g. the opencode Seatbelt engine) cannot touch
    --repo, so they stage finished files inside their own task directory and
    this check — which runs outside the worker sandbox — installs them before
    validating. Any refusal aborts the whole harvest with nothing copied, so a
    failed check never leaves stray files in the real repo. Everything fails
    closed: a symlinked stage root, a staged symlink or non-regular file, a
    destination that resolves through a symlink, .git/ paths, and unowned
    paths are all refused. Returns (installed_count, ok) and prints its own
    refusal/failure details.
    """
    if not stage_dir:
        return 0, True
    stage = pathlib.Path(stage_dir)
    if not stage.is_dir():
        return 0, True
    refusals: list[str] = []
    if stage.is_absolute():
        if stage.is_symlink():
            refusals.append(f"stage dir may not be a symlink: {stage}")
    elif stage.resolve() != pathlib.Path.cwd().resolve() / stage:
        refusals.append(f"stage dir resolves through a symlink; make {stage} a real directory inside the task dir")
    repo_resolved = repo.resolve()
    staged: list[tuple[pathlib.Path, str]] = []
    if not refusals:
        for dirpath, _dirnames, filenames in os.walk(stage):
            for name in filenames:
                src = pathlib.Path(dirpath) / name
                rel = src.relative_to(stage).as_posix()
                if src.is_symlink():
                    refusals.append(f"staged path is a symlink (stage regular file content instead): {rel}")
                    continue
                if not src.is_file():
                    refusals.append(f"staged path is not a regular file (FIFOs, sockets, devices are refused): {rel}")
                    continue
                if ".git" in rel.split("/"):
                    refusals.append(f"staged path may not touch .git: {rel}")
                    continue
                if not path_allowed(rel, owned):
                    refusals.append(f"staged file outside owned paths: {rel}")
                    continue
                dest = repo / rel
                if dest.is_dir():
                    refusals.append(f"staged file collides with an existing repo directory: {rel}")
                    continue
                if dest.resolve() != repo_resolved / rel:
                    refusals.append(f"staged path escapes the repo or resolves through a symlink: {rel}")
                    continue
                staged.append((src, rel))
    if refusals:
        print("FAIL: staged files refused; nothing was installed into the repo")
        for refusal in refusals:
            print(f" - {refusal}")
        print(f"stage only regular files inside your owned paths, at {stage_dir}/<repo-relative-path>")
        return 0, False
    installed: list[str] = []
    for src, rel in staged:
        dest = repo / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # copy, not copy2: a fresh destination mtime keeps make-style
            # incremental builds from treating harvested files as stale.
            shutil.copy(src, dest)
        except (OSError, shutil.Error) as exc:
            print(f"FAIL: harvest copy failed at {rel}: {exc}")
            if installed:
                print("files already installed before the failure (git status in the repo will show them):")
                for done in installed:
                    print(f" - {done}")
            return len(installed), False
        installed.append(rel)
        print(f"harvest: installed staged file into repo: {rel}")
    return len(installed), True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--owned", required=True, help="Comma-separated repo paths the worker may change")
    parser.add_argument("--allowed-status", default="", help="Additional comma-separated paths allowed in git status")
    parser.add_argument("--required-paths", default="", help="Comma-separated repo paths that must exist")
    parser.add_argument("--required-text", default="", help="Comma-separated text snippets that must appear somewhere in owned files")
    parser.add_argument("--build-command", required=True)
    parser.add_argument("--notes", default="notes.md")
    parser.add_argument(
        "--stage-dir",
        default="output/repo",
        help="Task-relative dir where write-confined workers stage files as <stage-dir>/<repo-relative-path>; "
        "installed into --repo (owned paths only) before validation. Pass '' to disable.",
    )
    args = parser.parse_args()

    repo = pathlib.Path(args.repo)
    fails: list[str] = []
    if not repo.exists():
        print(f"FAIL: repo path does not exist: {repo}")
        return 1
    if not (repo / ".git").exists():
        print(f"FAIL: repo path is not a git checkout: {repo}")
        return 1

    owned = split_csv(args.owned)
    allowed = owned + split_csv(args.allowed_status)
    if not owned:
        fails.append("no owned paths supplied to validator")

    harvested, harvest_ok = harvest_staged_files(args.stage_dir, repo, owned)
    if not harvest_ok:
        return 1

    notes = pathlib.Path(args.notes)
    if not notes.exists() or notes.stat().st_size == 0:
        fails.append(f"scratch notes file missing or empty: {notes}")

    required_paths = split_csv(args.required_paths)
    for rel in required_paths:
        if not (repo / rel).exists():
            fails.append(f"required repo path missing: {rel}")

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

    if fails:
        print("FAIL:")
        for fail in fails:
            print(f" - {fail}")
        print("skipping build/test command: the repo failed validation before the build")
        if args.stage_dir and not harvested:
            print(
                f"hint: if the repo is not writable from your sandbox, stage each changed file at "
                f"{args.stage_dir}/<repo-relative-path> — the verifier installs staged files into the repo "
                f"before validating"
            )
        return 1

    print(f"running build/test command in {repo}: {args.build_command}")
    proc = subprocess.run(args.build_command, cwd=repo, shell=True, capture_output=True, text=True, timeout=1800)
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
