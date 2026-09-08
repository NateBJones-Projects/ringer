#!/usr/bin/env python3
"""Retry-prompt construction (BLP-295).

Fixtures are captured from a real failed run — BLP-252, PR #223, the
`appointments-sort` task at /srv/swarm/blp-252 — with absolute paths under
/srv rewritten and nothing else changed:

* ``blp252_worker_log_attempt1.txt`` — the last 6000 bytes of the worker log as
  it stood when attempt 1's retry context was built. Its final 40 lines (what
  ``tail_text`` returns) are pure ``--output-format json`` accounting.
* ``blp252_check_output.txt`` — attempt 1's check output, recovered from the
  retry command that ringer logged for attempt 2. It is already truncated to
  the LAST of six vitest failures; the other five were cut by the very bug
  this module tests, so they exist nowhere on disk.
* ``blp252_vitest_attempt2.txt`` — attempt 2's vitest output (15 failures,
  10186 bytes), used where a check output larger than the cap is needed.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import build_failure_context  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WORKER_LOG = FIXTURES / "blp252_worker_log_attempt1.txt"
CHECK_OUTPUT = FIXTURES / "blp252_check_output.txt"
VITEST_ATTEMPT2 = FIXTURES / "blp252_vitest_attempt2.txt"

DIRTY_TREE_SENTENCE = "The tree carries the previous attempt's edits."


class BuildFailureContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmpdir = Path(self.tmp.name)

    def write_log(self, text: str) -> Path:
        path = self.tmpdir / "worker.log"
        path.write_text(text, encoding="utf-8")
        return path

    # --- ordering -----------------------------------------------------
    def test_check_output_comes_first(self) -> None:
        check = CHECK_OUTPUT.read_text(encoding="utf-8")
        context = build_failure_context(WORKER_LOG, check)
        self.assertTrue(
            context.startswith(check.strip()[:200]),
            f"context must open with the check output, got: {context[:200]!r}",
        )

    def test_surviving_attempt1_failure_detail_is_present(self) -> None:
        check = CHECK_OUTPUT.read_text(encoding="utf-8")
        context = build_failure_context(WORKER_LOG, check)
        # The single failure that survived, its cause, and the count line
        # proving five more were buried.
        self.assertIn("ReferenceError: isisNaNB is not defined", context)
        self.assertIn("[6/6]", context)
        self.assertIn("Tests  6 failed | 17 passed (23)", context)

    def test_first_failing_arms_and_run_header_survive_a_large_check(self) -> None:
        """The arms the old tail window destroyed are the ones worth keeping.

        A check output larger than the cap cannot arrive whole. The old code
        kept its last 6000 chars, which began mid-line, carried no vitest
        header, and had already lost arms 1 and 2 — the causal failures; the
        other thirteen here are the same ReferenceError cascading.
        """
        check = VITEST_ATTEMPT2.read_text(encoding="utf-8")
        context = build_failure_context(WORKER_LOG, check)
        self.assertIn("RUN  v2.1.9", context, "the run header orients the retry")
        for arm in ("[1/15]", "[2/15]", "[3/15]"):
            self.assertIn(arm, context, f"{arm} is a root-cause arm, not a cascade")

    # --- the stats block is dropped -----------------------------------
    def test_engine_json_accounting_is_absent(self) -> None:
        context = build_failure_context(WORKER_LOG, CHECK_OUTPUT.read_text(encoding="utf-8"))
        for token in ("session_id", "stats", "durationMs"):
            self.assertNotIn(token, context, f"{token!r} is engine accounting, not a failure")

    def test_worker_tail_without_stats_block_is_still_appended(self) -> None:
        log = self.write_log(
            "\n".join(f"worker line {n}" for n in range(1, 60))
            + "\nTypeError: cannot read property 'id' of undefined\n"
        )
        check = "CHECK FAIL: build FAILED (rc=1)"
        context = build_failure_context(log, check)
        self.assertTrue(context.startswith(check), "check output still leads")
        self.assertIn("TypeError: cannot read property 'id' of undefined", context)
        self.assertLess(
            context.index(check),
            context.index("TypeError:"),
            "worker tail must follow the check output, not precede it",
        )

    def test_ringer_scaffolding_lines_are_not_echoed_back(self) -> None:
        """An echoed marker must not look like a real one.

        Caught by tests/test_mock_engine.py: once the worker tail follows the
        check output it begins at a line boundary, so a quoted
        "[ringer.py] attempt 1 started" parsed as a genuine third attempt.
        The command: line is worse — it carries the entire previous prompt.
        """
        log = self.write_log(
            "[ringer.py] attempt 1 started 2026-09-07T22:11:56.061376+00:00\n"
            "[ringer.py] engine: gemini\n"
            "[ringer.py] command: some-engine --flag 'the entire previous spec'\n"
            "TypeError: cannot read property 'id' of undefined\n"
            "[ringer.py] attempt 1 exited rc=1\n"
        )
        context = build_failure_context(log, "CHECK FAIL: build FAILED (rc=1)")
        self.assertIn("TypeError: cannot read property 'id' of undefined", context)
        self.assertNotIn("[ringer.py]", context)
        self.assertNotIn("the entire previous spec", context)

    # --- dirty tree ---------------------------------------------------
    def test_dirty_tree_is_stated_when_not_in_worktrees_mode(self) -> None:
        repo = self.tmpdir / "repo"
        make_dirty_repo(repo)
        context = build_failure_context(
            WORKER_LOG,
            CHECK_OUTPUT.read_text(encoding="utf-8"),
            repo=repo,
            worktrees=False,
        )
        self.assertIn(DIRTY_TREE_SENTENCE, context)
        self.assertIn("tracked.txt", context, "a git diff --stat block must be present")
        self.assertTrue(
            context.rstrip().endswith(DIRTY_TREE_SENTENCE)
            or DIRTY_TREE_SENTENCE in context.rsplit("\n\n", 1)[-1],
            "the dirty-tree statement belongs at the end of the context",
        )

    def test_worktrees_mode_states_nothing_about_the_tree(self) -> None:
        repo = self.tmpdir / "repo"
        make_dirty_repo(repo)
        context = build_failure_context(
            WORKER_LOG,
            CHECK_OUTPUT.read_text(encoding="utf-8"),
            repo=repo,
            worktrees=True,
        )
        self.assertNotIn(DIRTY_TREE_SENTENCE, context)
        self.assertNotIn("tracked.txt", context)

    # --- the cap ------------------------------------------------------
    def test_over_the_cap_the_head_of_the_check_output_survives(self) -> None:
        check = VITEST_ATTEMPT2.read_text(encoding="utf-8")
        self.assertGreater(len(check), 6000, "fixture must exceed the per-section cap")
        context = build_failure_context(WORKER_LOG, check)
        head = check.strip().splitlines()[0]
        tail = check.strip().splitlines()[-1]
        self.assertIn(head, context, "the HEAD of the check output must survive the cap")
        self.assertNotIn(
            tail,
            context,
            "the tail is what gets cut now; the old code cut the head instead",
        )


def make_dirty_repo(repo: Path) -> None:
    import subprocess

    repo.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(repo), *a], check=True, capture_output=True
    )
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    run("add", "tracked.txt")
    run("commit", "-qm", "seed")
    (repo / "tracked.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
