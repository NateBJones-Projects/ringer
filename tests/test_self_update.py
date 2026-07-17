#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EvalConfig,
    UpdateConfig,
    hud_should_restart,
    maybe_self_update,
    perform_self_update,
    self_update_state_path,
)


class SelfUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin.git"
        self.upstream = self.root / "upstream"
        self.checkout = self.root / "checkout"
        self._git("init", "--bare", "--initial-branch=main", str(self.origin))
        self._git("init", "--initial-branch=main", str(self.upstream))
        self._configure_identity(self.upstream)
        (self.upstream / "ringer.py").write_text("print('v1')\n", encoding="utf-8")
        self._git("-C", str(self.upstream), "add", "ringer.py")
        self._git("-C", str(self.upstream), "commit", "-m", "initial")
        self._git("-C", str(self.upstream), "remote", "add", "origin", str(self.origin))
        self._git("-C", str(self.upstream), "push", "-u", "origin", "main")
        self._git("clone", str(self.origin), str(self.checkout))
        self._configure_identity(self.checkout)
        self.config = AppConfig(
            path=None,
            identity_default=None,
            state_dir=self.root / "state",
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=self.root / "state" / "runs.jsonl"),
            engines={},
            artifact=ArtifactConfig(
                enabled=True,
                out_template=str(self.root / "artifact.html"),
                report_template=str(self.root / "report.html"),
                index_out=self.root / "index.html",
            ),
            update=UpdateConfig(auto=True, check_interval_s=3600),
        )
        self.argv = [str(self.checkout / "ringer.py"), "lint", "manifest.json"]

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def _configure_identity(self, repo: Path) -> None:
        self._git("-C", str(repo), "config", "user.name", "Ringer Test")
        self._git("-C", str(repo), "config", "user.email", "ringer@example.test")

    def _add_upstream_commit(self, text: str = "print('v2')\n") -> str:
        target = self.upstream / "ringer.py"
        target.write_text(text, encoding="utf-8")
        self._git("-C", str(self.upstream), "add", "ringer.py")
        self._git("-C", str(self.upstream), "commit", "-m", "update")
        self._git("-C", str(self.upstream), "push", "origin", "main")
        return self._git("-C", str(self.upstream), "rev-parse", "HEAD").stdout.strip()

    def _run(self, **overrides: object):
        kwargs = {
            "config": self.config,
            "argv": self.argv,
            "repo_dir": self.checkout,
            "script_path": self.checkout / "ringer.py",
            "force": True,
            "allow_reexec": False,
        }
        kwargs.update(overrides)
        return perform_self_update(**kwargs)

    def _state_entry(self) -> dict[str, object]:
        data = json.loads(self_update_state_path(self.config.state_dir).read_text(encoding="utf-8"))
        return data[str(self.checkout.resolve())]

    def test_up_to_date_repo_is_a_no_op(self) -> None:
        before = self._git("-C", str(self.checkout), "rev-parse", "HEAD").stdout.strip()
        result = self._run()
        after = self._git("-C", str(self.checkout), "rev-parse", "HEAD").stdout.strip()
        self.assertEqual("up_to_date", result.status)
        self.assertEqual(before, after)
        self.assertEqual(0, self._state_entry()["behind"])

    def test_behind_clean_main_fast_forwards_and_requests_reexec(self) -> None:
        expected = self._add_upstream_commit()
        calls: list[tuple[str, list[str], dict[str, str]]] = []

        def fake_execve(executable: str, args: list[str], env: dict[str, str]) -> None:
            calls.append((executable, args, env))

        result = self._run(allow_reexec=True, execve=fake_execve, environ={"PATH": "/usr/bin"})
        actual = self._git("-C", str(self.checkout), "rev-parse", "HEAD").stdout.strip()
        self.assertEqual("applied", result.status)
        self.assertEqual(expected, actual)
        self.assertEqual(1, len(calls))
        self.assertEqual("1", calls[0][2]["RINGER_SELF_UPDATED"])
        self.assertEqual(self.argv[1:], calls[0][1][2:])

    def test_dirty_tracked_file_blocks_and_records_reason(self) -> None:
        self._add_upstream_commit()
        (self.checkout / "ringer.py").write_text("local edit\n", encoding="utf-8")
        result = self._run()
        self.assertEqual("blocked", result.status)
        self.assertEqual("tracked files are modified", result.reason)
        self.assertEqual(1, self._state_entry()["behind"])
        self.assertEqual(result.reason, self._state_entry()["reason"])

    def test_untracked_files_do_not_block_fast_forward(self) -> None:
        expected = self._add_upstream_commit()
        (self.checkout / "notes.local").write_text("keep me\n", encoding="utf-8")
        result = self._run()
        actual = self._git("-C", str(self.checkout), "rev-parse", "HEAD").stdout.strip()
        self.assertEqual("applied", result.status)
        self.assertEqual(expected, actual)
        self.assertTrue((self.checkout / "notes.local").is_file())

    def test_non_main_branch_blocks_apply(self) -> None:
        self._git("-C", str(self.checkout), "switch", "-c", "feature")
        self._add_upstream_commit()
        before = self._git("-C", str(self.checkout), "rev-parse", "HEAD").stdout.strip()
        result = self._run()
        self.assertEqual("blocked", result.status)
        self.assertIn("current branch is feature, not main", result.reason or "")
        self.assertEqual(before, self._git("-C", str(self.checkout), "rev-parse", "HEAD").stdout.strip())

    def test_diverged_local_main_blocks_without_creating_merge_commit(self) -> None:
        self._add_upstream_commit()
        (self.checkout / "local.txt").write_text("local\n", encoding="utf-8")
        self._git("-C", str(self.checkout), "add", "local.txt")
        self._git("-C", str(self.checkout), "commit", "-m", "local divergence")
        before = self._git("-C", str(self.checkout), "rev-parse", "HEAD").stdout.strip()
        result = self._run()
        after = self._git("-C", str(self.checkout), "rev-parse", "HEAD").stdout.strip()
        parents = self._git("-C", str(self.checkout), "show", "-s", "--format=%P", "HEAD").stdout.split()
        self.assertEqual("blocked", result.status)
        self.assertEqual("local main has diverged from origin/main", result.reason)
        self.assertEqual(before, after)
        self.assertEqual(1, len(parents))

    def test_second_startup_check_within_interval_runs_no_git_commands(self) -> None:
        calls: list[list[str]] = []

        def counting_runner(args: list[str], **kwargs: object):
            calls.append(args)
            return subprocess.run(args, **kwargs)

        now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
        first = maybe_self_update(
            self.argv,
            config=self.config,
            repo_dir=self.checkout,
            script_path=self.checkout / "ringer.py",
            runner=counting_runner,
            execve=lambda *_args: None,
            environ={},
            now=now,
        )
        first_count = len(calls)
        second = maybe_self_update(
            self.argv,
            config=self.config,
            repo_dir=self.checkout,
            script_path=self.checkout / "ringer.py",
            runner=counting_runner,
            execve=lambda *_args: None,
            environ={},
            now=now,
        )
        self.assertEqual("up_to_date", first.status)
        self.assertGreater(first_count, 0)
        self.assertEqual("throttled", second.status)
        self.assertEqual(first_count, len(calls))

    def test_forced_check_ignores_the_startup_throttle(self) -> None:
        first = self._run(force=False)
        calls: list[list[str]] = []

        def counting_runner(args: list[str], **kwargs: object):
            calls.append(args)
            return subprocess.run(args, **kwargs)

        second = self._run(force=True, runner=counting_runner)
        self.assertEqual("up_to_date", first.status)
        self.assertEqual("up_to_date", second.status)
        self.assertGreater(len(calls), 0)

    def test_self_updated_guard_short_circuits(self) -> None:
        result = maybe_self_update(self.argv, config=self.config, runner=self.fail_runner, environ={"RINGER_SELF_UPDATED": "1"})
        self.assertEqual("already restarted", result.reason)

    def test_no_self_update_environment_short_circuits(self) -> None:
        result = maybe_self_update(self.argv, config=self.config, runner=self.fail_runner, environ={"RINGER_NO_SELF_UPDATE": "1"})
        self.assertEqual("disabled by environment", result.reason)

    def test_no_self_update_argument_short_circuits(self) -> None:
        result = maybe_self_update([*self.argv, "--no-self-update"], config=self.config, runner=self.fail_runner, environ={})
        self.assertEqual("disabled for this invocation", result.reason)

    def test_config_auto_false_short_circuits(self) -> None:
        disabled = AppConfig(
            **{**self.config.__dict__, "update": UpdateConfig(auto=False, check_interval_s=3600)}
        )
        result = maybe_self_update(self.argv, config=disabled, runner=self.fail_runner, environ={})
        self.assertEqual("disabled by config", result.reason)

    def test_fetch_failure_is_recorded_and_does_not_raise(self) -> None:
        missing = self.root / "missing-origin.git"
        self._git("-C", str(self.checkout), "remote", "set-url", "origin", str(missing))
        result = self._run()
        self.assertEqual("error", result.status)
        self.assertIn("fetch failed", result.reason or "")
        self.assertIn("fetch failed", str(self._state_entry()["error"]))

    def test_git_check_failure_is_recorded_instead_of_reported_as_blocked(self) -> None:
        self._add_upstream_commit()

        def failing_runner(args: list[str], **kwargs: object):
            if "merge-base" in args:
                return subprocess.CompletedProcess(args, 128, "", "cannot inspect ancestry")
            return subprocess.run(args, **kwargs)

        result = self._run(runner=failing_runner)
        self.assertEqual("error", result.status)
        self.assertIn("fast-forward check failed", result.reason or "")
        self.assertIn("fast-forward check failed", str(self._state_entry()["error"]))

    def test_hud_restarts_when_disk_head_differs_from_running_head(self) -> None:
        self.assertTrue(hud_should_restart("old-head", "new-head"))
        self.assertFalse(hud_should_restart("same-head", "same-head"))

    @staticmethod
    def fail_runner(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("subprocess runner should not be called")


if __name__ == "__main__":
    unittest.main()
