#!/usr/bin/env python3
"""Every community contributor with merged work must appear in README Contributors.

Project rule (2026-07-16): community credit is never optional or manual-only.
This test audits git history two ways, because both merge styles occur here:

  * squash merges — the contributor is the commit AUTHOR (their display name);
  * merge commits — the contributor is the GitHub handle in the subject
    "Merge pull request #N from <handle>/<branch>".

Any identity found that is not a maintainer must appear in the README's
"## Contributors" section, by display name or by handle. Agents authoring
commits inside a contributor's PR (e.g. "Claude ...") are credited to the
PR's human, which the merge-subject side of the audit captures.
"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Maintainer/bot identities that are never listed as community contributors.
EXCLUDED_AUTHORS = {
    "jonathan edwards",
    "nate jones",
    "github",
}
EXCLUDED_AUTHOR_PREFIXES = ("claude",)
EXCLUDED_HANDLES = {
    "justfinethanku",
    "natebjones-projects",
}

MERGE_SUBJECT_RE = re.compile(r"^Merge pull request #\d+ from ([^/\s]+)/")


def git_lines(*args: str) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in proc.stdout.splitlines() if line.strip()]


def repo_has_history() -> bool:
    try:
        shallow = git_lines("rev-parse", "--is-shallow-repository")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return shallow and shallow[0].strip() == "false"


class ContributorCreditTests(unittest.TestCase):
    def test_every_merged_contributor_is_credited_in_readme(self) -> None:
        if not repo_has_history():
            self.fail(
                "git history unavailable or shallow — this audit needs full "
                "history (CI must checkout with fetch-depth: 0)"
            )

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        match = re.search(r"^## Contributors\b.*?$([\s\S]*?)(?=^## |\Z)", readme, re.MULTILINE)
        self.assertIsNotNone(match, "README.md has no '## Contributors' section")
        section = match.group(1).lower()

        missing: list[str] = []

        for line in git_lines("log", "--format=%an"):
            name = line.strip()
            lowered = name.lower()
            if lowered in EXCLUDED_AUTHORS:
                continue
            if lowered.startswith(EXCLUDED_AUTHOR_PREFIXES):
                continue
            if lowered not in section:
                missing.append(f"commit author: {name}")

        for line in git_lines("log", "--format=%s"):
            subject_match = MERGE_SUBJECT_RE.match(line.strip())
            if not subject_match:
                continue
            handle = subject_match.group(1)
            if handle.lower() in EXCLUDED_HANDLES:
                continue
            if handle.lower() not in section:
                missing.append(f"merged-PR handle: {handle}")

        self.assertFalse(
            sorted(set(missing)),
            "contributors with merged work are missing from README "
            f"'## Contributors': {sorted(set(missing))} — add them; "
            "community credit is a project rule, not a courtesy",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
