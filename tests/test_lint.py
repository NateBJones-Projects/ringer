#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import Manifest, TaskSpec, Verifier, lint_manifest  # noqa: E402


LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)

GOOD_CHECK = (
    "test -s output.txt && grep -q 'ready' output.txt || "
    "{ echo 'FAIL: output.txt missing or does not contain ready'; exit 1; }"
)


class LintManifestTests(unittest.TestCase):
    def manifest(
        self,
        tasks: list[dict[str, object]],
        *,
        worktrees: bool = False,
        max_parallel: int = 1,
    ) -> Manifest:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        obj: dict[str, object] = {
            "run_name": "lint-test",
            "workdir": str(Path(temp_dir.name) / "work"),
            "max_parallel": max_parallel,
            "worktrees": worktrees,
            "tasks": tasks,
        }
        if worktrees:
            obj["repo"] = temp_dir.name
        return Manifest.from_obj(obj)

    def task(
        self,
        key: str = "one",
        *,
        spec: str = LONG_SPEC,
        check: str = GOOD_CHECK,
        expect_files: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "key": key,
            "spec": spec,
            "check": check,
            "expect_files": ["output.txt"] if expect_files is None else expect_files,
            "verified": "the output file exists and contains the expected content",
        }

    def assertHasFinding(self, findings: list[str], expected: str) -> None:
        self.assertIn(expected, findings, f"expected lint finding not found: {expected}\nfindings: {findings}")

    def test_task_fields_must_be_strings(self) -> None:
        with self.assertRaisesRegex(ValueError, r"task one: check must be a string"):
            self.manifest([self.task(check=["cmd1", "cmd2"])])  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, r"task one: spec must be a string"):
            self.manifest([self.task(spec=["write it"])])  # type: ignore[arg-type]

        task = self.task()
        task["key"] = 123
        with self.assertRaisesRegex(ValueError, r"task key must be a string"):
            self.manifest([task])

    def test_w1_unverifiable_check(self) -> None:
        manifest = self.manifest([self.task(check="echo ok && echo done")])
        self.assertHasFinding(
            lint_manifest(manifest),
            "one: check cannot fail, so the task cannot be verified.",
        )

        commented_manifest = self.manifest([self.task(check="true # worker left the placeholder check")])
        self.assertHasFinding(
            lint_manifest(commented_manifest),
            "one: check cannot fail, so the task cannot be verified.",
        )

        quoted_hash_manifest = self.manifest(
            [
                self.task(
                    check=(
                        "test -s '#artifact' || "
                        "{ echo 'FAIL: #artifact missing'; exit 1; }"
                    )
                )
            ]
        )
        self.assertNotIn(
            "one: check cannot fail, so the task cannot be verified.",
            lint_manifest(quoted_hash_manifest),
        )

    def test_w2_silent_check(self) -> None:
        manifest = self.manifest([self.task(check="test -f output.txt && [ -s report.md ]")])
        self.assertHasFinding(
            lint_manifest(manifest),
            "one: check may fail without printing why; retry prompt and eval log depend on failure output.",
        )

        diff_manifest = self.manifest([self.task(check="diff -q expected.txt actual.txt")])
        self.assertHasFinding(
            lint_manifest(diff_manifest),
            "one: check may fail without printing why; retry prompt and eval log depend on failure output.",
        )

        diff_with_output = self.manifest(
            [self.task(check="diff -q a b || { echo FAIL; diff a b; exit 1; }")]
        )
        self.assertNotIn(
            "one: check may fail without printing why; retry prompt and eval log depend on failure output.",
            lint_manifest(diff_with_output),
        )

        grep_manifest = self.manifest([self.task(check="grep -q x file")])
        self.assertHasFinding(
            lint_manifest(grep_manifest),
            "one: check may fail without printing why; retry prompt and eval log depend on failure output.",
        )

        probe_chain_manifest = self.manifest([self.task(check="grep -q x file && test -s output.txt")])
        self.assertHasFinding(
            lint_manifest(probe_chain_manifest),
            "one: check may fail without printing why; retry prompt and eval log depend on failure output.",
        )

    def test_w3_worktree_deliverable_loss(self) -> None:
        manifest = self.manifest(
            [self.task(expect_files=["report.md"])],
            worktrees=True,
        )
        self.assertHasFinding(
            lint_manifest(manifest),
            "one: deliverable would be deleted with the worktree; write it outside the worktree or export it in the check.",
        )

    def test_w4_worktree_commit_loss(self) -> None:
        spec = LONG_SPEC + " After the file is correct, run git commit with a concise message."
        manifest = self.manifest(
            [self.task(spec=spec, expect_files=[])],
            worktrees=True,
        )
        self.assertHasFinding(
            lint_manifest(manifest),
            "one: worker commits die with the worktree; have the worker leave changes uncommitted and export the diff in the check.",
        )

        negated_spec = LONG_SPEC + " Do NOT run `git commit`; leave the worktree uncommitted."
        negated_manifest = self.manifest(
            [self.task(spec=negated_spec, expect_files=[])],
            worktrees=True,
        )
        self.assertNotIn(
            "one: worker commits die with the worktree; have the worker leave changes uncommitted and export the diff in the check.",
            lint_manifest(negated_manifest),
        )

    def test_w5_serial_fan_out(self) -> None:
        manifest = self.manifest(
            [
                self.task("one", expect_files=["one.txt"]),
                self.task("two", expect_files=["two.txt"]),
                self.task("three", expect_files=["three.txt"]),
            ],
            max_parallel=1,
        )
        self.assertHasFinding(
            lint_manifest(manifest),
            "manifest: tasks will run serially; set max_parallel.",
        )

    def test_w6_write_collision(self) -> None:
        manifest = self.manifest(
            [
                self.task("one", expect_files=["/tmp/shared-deliverable.txt"]),
                self.task("two", expect_files=["/tmp/shared-deliverable.txt"]),
            ],
            worktrees=False,
        )
        self.assertHasFinding(
            lint_manifest(manifest),
            "manifest: write collision on /tmp/shared-deliverable.txt: listed by one, two.",
        )

    def test_w6_relative_paths_do_not_collide(self) -> None:
        # Relative expect_files resolve inside each task's own directory —
        # many tasks emitting report.md/extraction.json is the NORMAL swarm
        # shape, not a collision (first field use caught this false positive).
        manifest = self.manifest(
            [
                self.task("one", expect_files=["report.md"]),
                self.task("two", expect_files=["report.md"]),
                self.task("three", expect_files=["report.md"]),
            ],
            worktrees=False,
            max_parallel=3,
        )
        self.assertEqual([], lint_manifest(manifest))

    def test_verifier_expands_user_expect_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            taskdir = Path(root) / "task"
            home = Path(root) / "home"
            taskdir.mkdir()
            home.mkdir()
            (home / "report.md").write_text("done\n", encoding="utf-8")
            previous_home = os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            try:
                task = TaskSpec(
                    key="one",
                    spec=LONG_SPEC,
                    check="true",
                    expect_files=("~/report.md",),
                )
                result = asyncio.run(Verifier().verify(task, taskdir))
            finally:
                if previous_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = previous_home
        self.assertTrue(result.ok, result.raw_output_excerpt)
        self.assertEqual((), result.missing_files)

    def test_w7_underspecified_spec(self) -> None:
        manifest = self.manifest([self.task(spec="Do it.")])
        self.assertHasFinding(
            lint_manifest(manifest),
            "one: spec is probably underspecified; workers are stateless and cannot ask questions.",
        )

    def test_w8_file_pointer_spec(self) -> None:
        findings = lint_manifest(
            self.manifest(
                [self.task(spec="Read the instructions at /tmp/brief.md and do exactly what it says in there.")]
            )
        )
        self.assertTrue(
            any("pointer to an instruction file" in item for item in findings),
            f"expected pointer-spec finding, got: {findings}",
        )

        # A long spec that references files as source material is fine.
        long_spec = (
            "You are a read-only reviewer. Study the code bundle at /tmp/bundle.txt as your "
            "source material, then write ./review.md with sections VERDICT, BLOCKERS, and "
            "EVIDENCE. For every blocker cite file and line from the bundle. Do not modify "
            "any file other than ./review.md. The review must judge correctness, security, "
            "and migration safety, and each claim needs a quoted line of code as evidence. "
            "If a concern cannot be verified from the bundle alone, list it under an "
            "UNCERTAIN heading instead of asserting it. Keep the verdict to one sentence. "
            "Write plainly; the reader is a busy maintainer deciding whether to merge today."
        )
        findings = lint_manifest(self.manifest([self.task(spec=long_spec, expect_files=["review.md"])]))
        self.assertFalse(
            any("pointer to an instruction file" in item for item in findings),
            f"long contextual spec should not be flagged: {findings}",
        )

    def test_w9_missing_expect_files(self) -> None:
        findings = lint_manifest(self.manifest([self.task(expect_files=[])]))
        self.assertTrue(
            any("no expect_files" in item for item in findings),
            f"expected missing-expect_files finding, got: {findings}",
        )

        # Worktrees mode legitimately exports deliverables outside the
        # taskdir (patch export), so the finding must not fire there.
        findings = lint_manifest(
            self.manifest([self.task(expect_files=[])], worktrees=True)
        )
        self.assertFalse(
            any("no expect_files" in item for item in findings),
            f"worktrees manifest should not be flagged for expect_files: {findings}",
        )

    ABSENCE_FINDING = (
        "one: check greps for a token's ABSENCE without stripping "
        "comments first; an explanatory comment containing the token will "
        "false-FAIL it — strip comments (sed/grep -v/awk) before asserting absence."
    )

    def test_negated_grep_over_file_fires(self) -> None:
        manifest = self.manifest(
            [self.task(check="! grep -q 'auth.uid' src/policy.sql")]
        )
        self.assertHasFinding(lint_manifest(manifest), self.ABSENCE_FINDING)

    def test_positive_grep_does_not_fire(self) -> None:
        manifest = self.manifest(
            [self.task(check="grep -q 'auth.uid' src/policy.sql")]
        )
        self.assertNotIn(self.ABSENCE_FINDING, lint_manifest(manifest))

    def test_stripped_comments_before_grep_does_not_fire(self) -> None:
        manifest = self.manifest(
            [self.task(check="! sed 's/#.*//' src/policy.sql | grep -q 'auth.uid'")]
        )
        self.assertNotIn(self.ABSENCE_FINDING, lint_manifest(manifest))

    def test_grep_over_pipe_output_does_not_fire(self) -> None:
        # The grep reads command output, not a file path — not the bug class.
        manifest = self.manifest(
            [self.task(check="! cat src/policy.sql | grep -q 'auth.uid'")]
        )
        self.assertNotIn(self.ABSENCE_FINDING, lint_manifest(manifest))

    def test_rg_recursive_is_unknown_and_abstains(self) -> None:
        # Regression: ripgrep has NO --recursive flag (recursion is its default;
        # real rg rejects it with 'unrecognized flag'). _RG_SPEC must NOT list
        # 'recursive', so parse_grep sees an unknown long option and ABSTAINS --
        # the fail-safe contract requires abstention, not a false absence-fire.
        manifest = self.manifest(
            [self.task(check="! rg --recursive auth.uid src/policy.sql")]
        )
        self.assertNotIn(self.ABSENCE_FINDING, lint_manifest(manifest))

    def test_absence_grep_semantics_table(self) -> None:
        # (check, should_fire, why) â grep-semantics-preserving classification.
        cases = [
            ("! grep -q 'auth.uid' src/policy.sql", True,
             "plain negated file grep is a comment-blind absence assertion"),
            ("! grep -L 'auth.uid' src/policy.sql", False,
             "-L selects files WITHOUT the token — negated, a presence assertion"),
            ("! grep --files-without-match 'auth.uid' src/policy.sql", False,
             "rg-style --files-without-match is the same presence assertion"),
            ("! grep -vq 'auth.uid' src/policy.sql", False,
             "combined -vq is a negative filter, not a plain absence grep"),
            ("! grep -lv 'auth.uid' src/policy.sql", False,
             "combined -lv is inverted (has -v) — not plain absence"),
            ("! grep -l 'auth.uid' src/policy.sql", True,
             "-l lists files WITH match; negated it is still an absence assertion"),
            ("grep -c 'auth.uid' src/policy.sql | grep -q '^0'", True,
             "count-is-zero pipeline is absence by another spelling"),
            ("grep -c 'auth.uid' src/policy.sql | sed 's/x//' | grep -q '^0'", True,
             "a strip AFTER the file grep cannot strip source comments — must fire"),
            ("! sed 's/#.*//' src/policy.sql | grep -q 'auth.uid'", False,
             "comment strip BEFORE the grep makes it safe"),
            ("! cat src/policy.sql | grep -q 'auth.uid'", False,
             "grep reads piped output, not a file path"),
            ("! grep -e '-auth.uid' src/policy.sql", True,
             "a pattern beginning with '-' via -e is an operand, not a flag"),
            ("test -f src/policy.sql && ! grep -q 'auth.uid' src/policy.sql", True,
             "one &&-joined segment is the absence grep"),
            ("! grep -q 'auth.uid' src/policy.sql ; test -s report.md", True,
             "one ;-joined segment is the absence grep"),
            ("! rg -q 'auth.uid' src/policy.sql", True,
             "plain negated rg over a file is a comment-blind absence assertion"),
            ("! cat src/policy.sql | rg -g '*.sql' -q 'auth.uid'", False,
             "rg -g/--glob consumes '*.sql' as its value; rg reads piped "
             "output, not a file path"),
            ("! rg -g '*.sql' -q 'auth.uid' src/policy.sql", True,
             "rg -g eats the glob, leaving pattern+file operands -- a direct "
             "file absence grep"),
            ("! rg -t sql -q 'auth.uid' src/policy.sql", True,
             "rg -t/--type takes a value; pattern+file remain operands"),
            ("! rg --recursive auth.uid src/policy.sql", False,
             "rg has NO --recursive flag (recursion is its default) -- unknown "
             "long option makes the stage ambiguous, so parse_grep abstains"),
            ("! grep --color 'auth.uid' src/policy.sql", True,
             "GNU grep --color takes an OPTIONAL arg (never the next token); "
             "pattern+file stay operands"),
            ("! grep --color=always 'auth.uid' src/policy.sql", True,
             "--color=always binds inline; pattern+file stay operands"),
            ("! grep --colour 'auth.uid' src/policy.sql", True,
             "--colour is the same optional-arg spelling"),
            # FAIL-SAFE: unknown option -> abstain (parse_grep returns None),
            # so the rule cannot false-fire on a table gap. The enumerated
            # tables list what we positively recognize, not everything grep/rg
            # accept -- three rounds each surfaced another missing value option.
            ("! cat src/policy.sql | rg -j 1 -q 'auth.uid'", False,
             "rg -j/--threads takes a value ('1'); rg reads piped output, "
             "not a file path"),
            ("! rg -j 1 'auth.uid' src/policy.sql", True,
             "rg -j eats its thread count, leaving pattern+file operands -- a "
             "direct file absence grep"),
            # INTERSECTION CONTRACT: GNU-only spellings that BSD grep rejects are
            # left UNLISTED, so a file-target invocation using them abstains
            # instead of false-firing on macOS. --exclude-from / --group-separator
            # are the reviewer-reproduced cases (BSD grep 2.6.0 rejects both).
            ("! grep --exclude-from ignore 'auth.uid' src/policy.sql", False,
             "--exclude-from is GNU-only (BSD grep rejects it) so it is unlisted; "
             "parse_grep abstains rather than assume it swallows 'ignore' -- no "
             "false fire on a spelling only GNU recognizes"),
            ("! grep --group-separator=-- 'auth.uid' src/policy.sql", False,
             "--group-separator is GNU-only (BSD grep rejects it) so it is "
             "unlisted; the inline =-- cannot be trusted, parse_grep abstains"),
            ("! cat src/policy.sql | grep --exclude-from ignore -q 'auth.uid'", False,
             "GNU-only --exclude-from is unlisted -> abstain; grep also reads "
             "piped output here, so no fire either way"),
            ("! grep --frobnicate 'auth.uid' src/policy.sql", False,
             "unknown long option makes the stage ambiguous -- parse_grep "
             "abstains rather than guess whether it swallows a value"),
            ("! grep -Q 'auth.uid' src/policy.sql", False,
             "unknown short flag -Q makes the stage ambiguous -- abstain"),
            ("! grep --context 1 auth.uid f", False,
             "grep --context arity diverges (GNU takes a separate NUM, BSD "
             "binds only via --context=NUM), so it is unlisted -- parse_grep "
             "abstains rather than guess whether '1' is a value or the pattern"),
            ("! grep --color 'auth.uid' src/policy.sql", True,
             "known optional-arg --color still fires (regression guard)"),
            ("! grep -q 'auth.uid' src/policy.sql", True,
             "all-known flags: plain negated file grep still fires"),
            ("! rg -q 'auth.uid' src/policy.sql", True,
             "all-known rg flags: plain negated file grep still fires"),
        ]
        for check, should_fire, why in cases:
            with self.subTest(check=check):
                findings = lint_manifest(self.manifest([self.task(check=check)]))
                if should_fire:
                    self.assertIn(self.ABSENCE_FINDING, findings, why)
                else:
                    self.assertNotIn(self.ABSENCE_FINDING, findings, why)

    def test_compliant_manifest_is_clean(self) -> None:
        manifest = self.manifest(
            [
                self.task("one", expect_files=["one.txt"]),
                self.task("two", expect_files=["two.txt"]),
                self.task("three", expect_files=["three.txt"]),
            ],
            max_parallel=2,
        )
        self.assertEqual([], lint_manifest(manifest), "compliant manifest should have no lint findings")

    def test_templates_are_clean(self) -> None:
        # Every kit ships one or more manifest skeletons (manifest.json plus
        # optional manifest-round*.json for multi-round kits).
        template_paths = sorted((ROOT / "templates").glob("*/manifest*.json"))
        self.assertTrue(template_paths, "expected templates/*/manifest*.json files to exist")
        for path in template_paths:
            with self.subTest(template=path.name):
                manifest = Manifest.from_path(path)
                findings = lint_manifest(manifest)
                self.assertEqual([], findings, f"{path} should lint clean, got: {findings}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
