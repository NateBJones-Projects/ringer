#!/usr/bin/env python3
"""`run --baseline` executes the checks against the unmodified tree, no workers.

Observed in the field (2026-07-14): two of twelve fix-swarm lanes failed on
verify assertions that were wrong about the PRE-change tree — one of them
unsatisfiable on a pristine repo. An honest worker burned ~100k tokens
against it. Running every check once, before any worker spawns, makes
"which of my assertions are already wrong?" a one-command question.

Pinned here:
  * checks run against fresh scratch taskdirs (detached worktrees when the
    manifest uses worktrees) and report pass/FAIL with output excerpts
  * no workers spawn — a deliberately broken engine binary must not matter,
    and the engine preflight must not block baseline
  * no model-log rows are written
  * no scratch worktrees or taskdirs leak into the manifest workdir or repo
  * exit code 0 — baseline reports; the orchestrator judges
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def toml_string(value: object) -> str:
    return json.dumps(str(value))


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text("hello baseline\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    for args in (
        ["git", "-C", str(path), "init", "--quiet"],
        ["git", "-C", str(path), "add", "README.md"],
        ["git", "-C", str(path), "commit", "--quiet", "-m", "init"],
    ):
        subprocess.run(args, check=True, env=env, capture_output=True)


class BaselineModeTests(unittest.TestCase):
    def test_baseline_runs_checks_without_spawning_workers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            ringer_home = root / "ringer-home"
            state_dir = root / "state"
            workdir = root / "work"
            repo = root / "repo"
            config_path = root / "config.toml"
            manifest_path = root / "manifest.json"
            model_log = root / "runs.jsonl"

            home.mkdir()
            ringer_home.mkdir()
            init_git_repo(repo)

            # The engine binary does not exist. Baseline must neither spawn
            # it nor be blocked by the startup engine preflight.
            config_path.write_text(
                "\n".join(
                    [
                        f"state_dir = {toml_string(state_dir)}",
                        "",
                        "[eval]",
                        'backend = "jsonl"',
                        f"jsonl_path = {toml_string(model_log)}",
                        "",
                        "[artifact]",
                        "enabled = false",
                        "",
                        "[engines.missing]",
                        'bin = "/nonexistent/engine-binary"',
                        "args_template = [",
                        '  "{spec}",',
                        "]",
                        "sandbox_args = []",
                        "full_access_args = []",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "baseline-test",
                        "workdir": str(workdir),
                        "max_parallel": 2,
                        "worktrees": True,
                        "repo": str(repo),
                        "tasks": [
                            {
                                "key": "unchanged-behavior",
                                "engine": "missing",
                                "spec": "Placeholder spec; baseline never spawns a worker.",
                                # Asserts something already true of the tree —
                                # the kind of assertion that MUST pass baseline.
                                "check": (
                                    "grep -q 'hello baseline' README.md || "
                                    "{ echo FAIL: README.md lost its content; exit 1; }"
                                ),
                            },
                            {
                                "key": "new-behavior",
                                "engine": "missing",
                                "spec": "Placeholder spec; baseline never spawns a worker.",
                                # Demands a file a worker would create — the
                                # kind of assertion EXPECTED to fail baseline.
                                "check": (
                                    "test -f built-by-worker.txt || "
                                    "{ echo FAIL: built-by-worker.txt not present; exit 1; }"
                                ),
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["RINGER_HOME"] = str(ringer_home)
            env["XDG_CONFIG_HOME"] = str(root / "xdg-config")

            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "run",
                    str(manifest_path),
                    "--config",
                    str(config_path),
                    "--no-dashboard",
                    "--baseline",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )

            combined_output = proc.stdout + proc.stderr

            # Baseline reports; it does not judge. Exit 0 even with failures.
            self.assertEqual(0, proc.returncode, combined_output)

            self.assertRegex(
                combined_output,
                re.compile(
                    r"^unchanged-behavior\s+baseline: pass \(rc=0\)", re.MULTILINE
                ),
                combined_output,
            )
            self.assertRegex(
                combined_output,
                re.compile(r"^new-behavior\s+baseline: FAIL \(rc=1\)", re.MULTILINE),
                combined_output,
            )
            # The failing check's WHY is excerpted.
            self.assertIn("built-by-worker.txt not present", combined_output)
            # The guidance that makes the report readable.
            self.assertIn("fix the check before spawning", combined_output)
            self.assertIn("1 pass, 1 fail, 0 error of 2 check(s)", combined_output)

            # No workers, no model rows, no leaked scratch state.
            self.assertFalse(model_log.exists(), "baseline wrote model-log rows")
            self.assertFalse(
                (workdir / "unchanged-behavior").exists()
                or (workdir / "new-behavior").exists(),
                "baseline leaked taskdirs into the manifest workdir",
            )
            worktree_list = subprocess.run(
                ["git", "-C", str(repo), "worktree", "list"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(
                1,
                len(worktree_list.splitlines()),
                f"baseline leaked worktrees:\n{worktree_list}",
            )


class BaselineContainmentTests(unittest.TestCase):
    def test_task_key_cannot_escape_baseline_scratch_root(self) -> None:
        import asyncio
        import contextlib
        import importlib.util
        import io

        spec = importlib.util.spec_from_file_location("ringer_baseline_test", ROOT / "ringer.py")
        assert spec is not None and spec.loader is not None
        ringer = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = ringer
        spec.loader.exec_module(ringer)

        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manifest = ringer.Manifest.from_obj(
                {
                    "run_name": "baseline-escape-test",
                    "workdir": str(root / "work"),
                    "tasks": [
                        {
                            "key": "../escape",
                            "spec": "Placeholder; baseline spawns nothing.",
                            "check": "touch escaped.txt",
                        }
                    ],
                }
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                rc = asyncio.run(ringer.run_baseline(manifest, config=None))
            output = buffer.getvalue()
            self.assertEqual(0, rc, output)
            self.assertIn("task key escapes the baseline scratch root", output)
            self.assertIn("0 pass, 0 fail, 1 error of 1 check(s)", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
