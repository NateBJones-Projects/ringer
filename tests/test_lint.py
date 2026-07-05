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
        template_paths = sorted((ROOT / "templates").glob("*.json"))
        self.assertTrue(template_paths, "expected templates/*.json files to exist")
        for path in template_paths:
            with self.subTest(template=path.name):
                manifest = Manifest.from_path(path)
                findings = lint_manifest(manifest)
                self.assertEqual([], findings, f"{path} should lint clean, got: {findings}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
