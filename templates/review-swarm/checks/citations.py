#!/usr/bin/env python3
"""Every file:line citation in a review report must point at a real file and line,
and a double-quoted phrase that follows a citation must appear within the cited
line range (±2 lines) of THAT file. Backticked tokens are paths, not quotes."""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
CITE = re.compile(r"(?<![\w/~])((?:\.claude|docs|scripts|src|tests|ui|deploy|config|demos|bin|pyproject\.toml|CLAUDE\.md|ONBOARDING\.md|README\.md|SECURITY\.md|\.pre-commit-config\.yaml|\.gitignore|\.env\.example)[\w./-]*):(\d+)(?:-(\d+))?")
QUOTE = re.compile(r'["“]([^"”\n]{8,})["”]')
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("`", "").replace("*", "").replace("\\", "")).strip().lower()
def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--report", required=True, type=Path); ap.add_argument("--repo", required=True, type=Path)
    a = ap.parse_args(); text = a.report.read_text(encoding="utf-8")
    bad: list[str] = []; n_cites = 0; n_quotes = 0
    if re.search(r"(?:~|/home/\w+)/\.claude/", text):
        bad.append("report cites the user's global ~/.claude tree; the surface is the repo's own CLAUDE.md and .claude/skills")
    for line in text.splitlines():
        if not line.lstrip().startswith("Evidence:"):
            continue
        cites = [(m.start(), m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))) for m in CITE.finditer(line)]
        if not cites:
            continue
        for _, rel, lo, hi in cites:
            n_cites += 1; p = a.repo / rel
            if not p.is_file():
                bad.append(f"{rel}:{lo} -> file does not exist"); continue
            n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
            if lo < 1 or hi > n:
                bad.append(f"{rel}:{lo}-{hi} -> file has only {n} lines")
        for qm in QUOTE.finditer(line):
            owners = [c for c in cites if c[0] < qm.start()]
            if not owners:
                continue
            _, rel, lo, hi = owners[-1]; p = a.repo / rel
            if not p.is_file():
                continue
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            window = norm(" ".join(lines[max(0, lo - 3): min(len(lines), hi + 2)]))
            q = norm(qm.group(1)); n_quotes += 1
            if q[:40] not in window:
                bad.append(f"{rel}:{lo} -> quoted text not within the cited lines (±2): {q[:60]!r}")
    findings = text.count("### Finding:")
    if findings and not n_cites:
        bad.append("report has findings but no file:line citations in Evidence lines")
    for b in bad: print("FAIL [citation]:", b)
    if bad: return 1
    print(f"PASS [citation]: {n_cites} citations resolve; {n_quotes} quotes found within their cited lines ({findings} findings)"); return 0
if __name__ == "__main__": sys.exit(main())
