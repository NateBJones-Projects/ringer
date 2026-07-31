#!/usr/bin/env python3
"""worktree_provision: declared gitignored dependency dirs for fresh worktrees.

`git worktree add` starts clean, so anything gitignored (node_modules, build
caches) is absent and a task whose build or check depends on it fails before
the worker starts. Manifests declare those paths; the runner clones each from
the primary checkout into the new worktree, and a declaration that cannot be
honored fails taskdir preparation loudly instead of handing the worker a
half-provisioned tree.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import Manifest, RingerRunner, TaskRuntime, TaskSpec  # noqa: E402


def _runtime(tmp: Path) -> TaskRuntime:
    log = tmp / "worker.log"
    log.touch()
    return TaskRuntime(
        task=TaskSpec.from_obj({"key": "t", "spec": "s", "check": "true"}),
        taskdir=tmp,
        log_path=log,
    )


def _manifest(tmp: Path, repo: Path, **extra) -> Manifest:
    src = tmp / "manifest.json"
    src.write_text(
        json.dumps(
            {
                "run_name": "t",
                "workdir": str(tmp),
                "max_parallel": 1,
                "worktrees": True,
                "repo": str(repo),
                "tasks": [{"key": "t", "spec": "s", "check": "true"}],
                **extra,
            }
        ),
        encoding="utf-8",
    )
    return Manifest.from_path(src)


def _runner(tmp: Path, repo: Path, **extra) -> RingerRunner:
    runner = RingerRunner.__new__(RingerRunner)
    runner.manifest = _manifest(tmp, repo, **extra)
    return runner


class ManifestFieldTests(unittest.TestCase):
    def test_declared_field_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            manifest = _manifest(tmp, tmp, worktree_provision=["node_modules"])
            self.assertEqual(manifest.worktree_provision, ("node_modules",))

    def test_absent_field_defaults_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self.assertEqual(_manifest(tmp, tmp).worktree_provision, ())

    def test_field_must_be_a_list_of_nonempty_strings(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            for bad in (["node_modules", 7], "node_modules", [""]):
                with self.assertRaisesRegex(ValueError, "list of non-empty strings"):
                    _manifest(tmp, tmp, worktree_provision=bad)

    def test_absolute_and_traversal_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            for bad in ("/etc", "C:\\deps", "../outside", "a/../../b"):
                with self.assertRaisesRegex(ValueError, "repo-relative"):
                    _manifest(tmp, tmp, worktree_provision=[bad])

    def test_field_requires_worktrees_mode(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            src = tmp / "manifest.json"
            src.write_text(
                json.dumps(
                    {
                        "run_name": "t",
                        "workdir": str(tmp),
                        "worktrees": False,
                        "tasks": [{"key": "t", "spec": "s", "check": "true"}],
                        "worktree_provision": ["node_modules"],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "requires worktrees"):
                Manifest.from_path(src)


class ProvisionBehaviourTests(unittest.TestCase):
    def test_declared_dir_is_cloned_with_contents(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo, wt = tmp / "repo", tmp / "wt"
            (repo / "web" / "node_modules" / "pkg").mkdir(parents=True)
            (repo / "web" / "node_modules" / "pkg" / "index.js").write_text("x")
            wt.mkdir()
            runner = _runner(tmp, repo, worktree_provision=["web/node_modules"])
            err = asyncio.run(runner._provision_worktree_deps(_runtime(tmp), wt))
            self.assertIsNone(err)
            self.assertEqual(
                (wt / "web" / "node_modules" / "pkg" / "index.js").read_text(),
                "x",
                "contents must be cloned, not just the directory created",
            )

    def test_absent_field_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo, wt = tmp / "repo", tmp / "wt"
            repo.mkdir()
            wt.mkdir()
            err = asyncio.run(
                _runner(tmp, repo)._provision_worktree_deps(_runtime(tmp), wt)
            )
            self.assertIsNone(err)
            self.assertEqual(list(wt.iterdir()), [])

    def test_missing_source_fails_preparation_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo, wt = tmp / "repo", tmp / "wt"
            repo.mkdir()
            wt.mkdir()
            err = asyncio.run(
                _runner(tmp, repo, worktree_provision=["nope"])
                ._provision_worktree_deps(_runtime(tmp), wt)
            )
            self.assertIsNotNone(
                err, "a half-provisioned worktree must not be reported as success"
            )
            self.assertIn("nope", err)

    def test_existing_destination_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo, wt = tmp / "repo", tmp / "wt"
            (repo / "deps").mkdir(parents=True)
            (repo / "deps" / "new.txt").write_text("from repo")
            (wt / "deps").mkdir(parents=True)
            (wt / "deps" / "old.txt").write_text("already here")
            err = asyncio.run(
                _runner(tmp, repo, worktree_provision=["deps"])
                ._provision_worktree_deps(_runtime(tmp), wt)
            )
            self.assertIsNone(err)
            self.assertTrue((wt / "deps" / "old.txt").exists())
            self.assertFalse(
                (wt / "deps" / "new.txt").exists(),
                "an existing destination must not be overwritten",
            )

    def test_symlinked_source_may_not_escape_the_repo(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo, wt, outside = tmp / "repo", tmp / "wt", tmp / "outside"
            repo.mkdir()
            wt.mkdir()
            outside.mkdir()
            (outside / "secret.txt").write_text("private")
            os.symlink(outside, repo / "deps")
            err = asyncio.run(
                _runner(tmp, repo, worktree_provision=["deps"])
                ._provision_worktree_deps(_runtime(tmp), wt)
            )
            self.assertIsNotNone(err, "a symlink out of the repo must be rejected")
            self.assertFalse((wt / "deps").exists())


if __name__ == "__main__":
    unittest.main()
