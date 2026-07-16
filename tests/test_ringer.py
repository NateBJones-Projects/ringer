from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RINGER_PATH = ROOT / "ringer.py"
SPEC = importlib.util.spec_from_file_location("ringer_module", RINGER_PATH)
assert SPEC is not None and SPEC.loader is not None
ringer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ringer
SPEC.loader.exec_module(ringer)


class RingerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="ringer-test-")
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "config.toml"
        self.jsonl_path = self.root / "runs.jsonl"
        self.state_dir = self.root / "state"
        self.write_config(
            {
                "write_done": ["-c", "printf done > out.txt"],
                "write_empty": ["-c", ": > out.txt"],
                "write_wrong_file": ["-c", "printf done > wrong.txt"],
                "sleep_then_write": ["-c", "echo $$ > worker.pid; sleep 30; printf done > out.txt"],
                "ignore_term": ["-c", "trap '' TERM; echo $$ > worker.pid; while :; do sleep 1; done"],
                "spec_shell": ["-c", "{spec}"],
                "token_printer": ["-c", "printf done > out.txt; echo 'tokens used: 1,234'"],
            }
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_config(self, engines: dict[str, list[str]], *, port: int = 18787) -> None:
        lines = [
            f'state_dir = "{self.state_dir}"',
            f"dashboard_port_base = {port}",
            "allow_full_access = false",
            "",
            "[eval]",
            'backend = "jsonl"',
            f'jsonl_path = "{self.jsonl_path}"',
            "",
        ]
        for name, args_template in engines.items():
            lines.extend(
                [
                    f"[engines.{name}]",
                    'bin = "/bin/sh"',
                    f"args_template = {json.dumps(args_template)}",
                    "sandbox_args = []",
                    "full_access_args = []",
                    'token_regex = "tokens\\\\s+used\\\\s*:?\\\\s*([0-9][0-9,]*)"',
                    "",
                ]
            )
        self.config_path.write_text("\n".join(lines), encoding="utf-8")

    def write_manifest(self, name: str, manifest: dict[str, object]) -> Path:
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path

    def manifest(self, name: str, task: dict[str, object], **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "run_name": name,
            "workdir": str(self.root / f"work-{name}"),
            "max_parallel": 1,
            "tasks": [task],
        }
        data.update(overrides)
        return data

    def run_ringer(
        self,
        manifest: Path,
        *,
        config_path: Path | None = None,
        no_dashboard: bool = True,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [
            sys.executable,
            "-B",
            str(RINGER_PATH),
            "--config",
            str(config_path or self.config_path),
            "run",
            str(manifest),
            "--identity",
            "test-runner",
        ]
        if no_dashboard:
            cmd.append("--no-dashboard")
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["RINGER_NO_SELF_UPDATE"] = "1"
        return subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )

    def read_rows(self, path: Path | None = None) -> list[dict[str, object]]:
        jsonl_path = path or self.jsonl_path
        if not jsonl_path.exists():
            return []
        return [
            json.loads(line)
            for line in jsonl_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def read_final_state(self) -> dict[str, object]:
        state_files = sorted((self.state_dir / "runs").glob("*.json"))
        self.assertEqual(len(state_files), 1)
        return json.loads(state_files[0].read_text(encoding="utf-8"))

    @staticmethod
    def pid_is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def test_failing_check_output_is_logged_and_injected_into_retry(self) -> None:
        manifest = self.write_manifest(
            "diagnostic-fail",
            self.manifest(
                "diagnostic-fail",
                {
                    "key": "diag",
                    "engine": "write_done",
                    "spec": "Write done.",
                    "expect_files": ["out.txt"],
                    "check": (
                        'actual=$(cat out.txt 2>/dev/null); '
                        'test "$actual" = expected || '
                        '{ echo "expected=expected actual=$actual"; exit 1; }'
                    ),
                },
            ),
        )

        result = self.run_ringer(manifest)

        self.assertEqual(result.returncode, 1, result.stdout)
        rows = self.read_rows()
        self.assertEqual([row["verdict"] for row in rows], ["FAIL", "FAIL"])
        self.assertIn("expected=expected actual=done", rows[0]["notes"])
        self.assertIn("Previous attempt failed", rows[1]["spec"])
        self.assertIn("expected=expected actual=done", rows[1]["spec"])

    def test_missing_expected_file_fails_even_when_check_passes(self) -> None:
        manifest = self.write_manifest(
            "missing-file",
            self.manifest(
                "missing-file",
                {
                    "key": "missing",
                    "engine": "write_wrong_file",
                    "spec": "Write the wrong file.",
                    "expect_files": ["out.txt"],
                    "check": "true",
                },
            ),
        )

        result = self.run_ringer(manifest)

        self.assertEqual(result.returncode, 1, result.stdout)
        rows = self.read_rows()
        self.assertEqual([row["verdict"] for row in rows], ["FAIL", "FAIL"])
        self.assertIn('missing_expect_files=["out.txt"]', rows[0]["notes"])
        self.assertIn("[ringer] missing expected files: out.txt", rows[0]["notes"])

    def test_empty_expected_file_is_treated_as_missing(self) -> None:
        manifest = self.write_manifest(
            "empty-file",
            self.manifest(
                "empty-file",
                {
                    "key": "empty",
                    "engine": "write_empty",
                    "spec": "Write an empty file.",
                    "expect_files": ["out.txt"],
                    "check": "test -f out.txt",
                },
            ),
        )

        result = self.run_ringer(manifest)

        self.assertEqual(result.returncode, 1, result.stdout)
        rows = self.read_rows()
        self.assertEqual([row["verdict"] for row in rows], ["FAIL", "FAIL"])
        self.assertIn('missing_expect_files=["out.txt"]', rows[0]["notes"])

    def test_timeout_retries_once_and_reports_timeout(self) -> None:
        manifest = self.write_manifest(
            "timeout",
            self.manifest(
                "timeout",
                {
                    "key": "timeout",
                    "engine": "sleep_then_write",
                    "spec": "Sleep too long.",
                    "expect_files": ["out.txt"],
                    "timeout_s": 1,
                    "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                },
            ),
        )

        result = self.run_ringer(manifest, timeout=10)

        self.assertEqual(result.returncode, 1, result.stdout)
        rows = self.read_rows()
        self.assertEqual([row["verdict"] for row in rows], ["TIMEOUT", "TIMEOUT"])
        self.assertIn("retry=true", rows[1]["notes"])
        self.assertIn("worker_returncode=-15", rows[0]["notes"])

    def test_sigterm_cleans_up_active_worker_and_finishes_state(self) -> None:
        manifest = self.write_manifest(
            "sigterm",
            self.manifest(
                "sigterm",
                {
                    "key": "term",
                    "engine": "sleep_then_write",
                    "spec": "Sleep until terminated.",
                    "expect_files": ["out.txt"],
                    "timeout_s": 30,
                    "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                },
            ),
        )
        cmd = [
            sys.executable,
            "-B",
            str(RINGER_PATH),
            "--config",
            str(self.config_path),
            "run",
            str(manifest),
            "--no-dashboard",
            "--identity",
            "test-runner",
        ]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["RINGER_NO_SELF_UPDATE"] = "1"
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        worker_pid_path = self.root / "work-sigterm" / "term" / "worker.pid"
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not worker_pid_path.exists():
                time.sleep(0.05)
            self.assertTrue(worker_pid_path.exists())
            worker_pid = int(worker_pid_path.read_text(encoding="utf-8").strip())
            proc.send_signal(signal.SIGTERM)
            stdout, _ = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                stdout, _ = proc.communicate(timeout=10)

        self.assertEqual(proc.returncode, 130, stdout)
        self.assertFalse(self.pid_is_alive(worker_pid), stdout)
        state = self.read_final_state()
        self.assertTrue(state["finished"])
        self.assertEqual(state["state"], "finished")
        self.assertEqual(state["summary"]["fail"], 1)
        self.assertEqual(state["tasks"][0]["status"], "fail")

    def test_second_signal_during_shutdown_does_not_cancel_cleanup(self) -> None:
        manifest = self.write_manifest(
            "resignal",
            self.manifest(
                "resignal",
                {
                    "key": "term",
                    "engine": "ignore_term",
                    "spec": "Ignore SIGTERM until killed.",
                    "expect_files": ["out.txt"],
                    "timeout_s": 30,
                    "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                },
            ),
        )
        cmd = [
            sys.executable,
            "-B",
            str(RINGER_PATH),
            "--config",
            str(self.config_path),
            "run",
            str(manifest),
            "--no-dashboard",
            "--identity",
            "test-runner",
        ]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["RINGER_NO_SELF_UPDATE"] = "1"
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        worker_pid_path = self.root / "work-resignal" / "term" / "worker.pid"
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not worker_pid_path.exists():
                time.sleep(0.05)
            self.assertTrue(worker_pid_path.exists())
            worker_pid = int(worker_pid_path.read_text(encoding="utf-8").strip())
            proc.send_signal(signal.SIGTERM)
            # The worker traps TERM, so cleanup is held in the 1s TERM->KILL
            # escalation window; a second signal lands mid-cleanup.
            time.sleep(0.3)
            proc.send_signal(signal.SIGTERM)
            stdout, _ = proc.communicate(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()
                stdout, _ = proc.communicate(timeout=10)

        self.assertEqual(proc.returncode, 130, stdout)
        self.assertIn("shutdown already in progress", stdout)
        self.assertNotIn("Traceback", stdout)
        self.assertFalse(self.pid_is_alive(worker_pid), stdout)
        state = self.read_final_state()
        self.assertTrue(state["finished"])
        self.assertEqual(state["state"], "finished")
        self.assertEqual(state["tasks"][0]["status"], "fail")

    def test_custom_shell_engine_substitutes_spec_placeholder(self) -> None:
        manifest = self.write_manifest(
            "custom-shell",
            self.manifest(
                "custom-shell",
                {
                    "key": "custom",
                    "engine": "spec_shell",
                    "spec": "printf done > out.txt",
                    "expect_files": ["out.txt"],
                    "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                },
            ),
        )

        result = self.run_ringer(manifest)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([row["verdict"] for row in self.read_rows()], ["PASS"])

    def test_token_regex_captures_worker_tokens(self) -> None:
        manifest = self.write_manifest(
            "tokens",
            self.manifest(
                "tokens",
                {
                    "key": "tokens",
                    "engine": "token_printer",
                    "spec": "Print token count.",
                    "expect_files": ["out.txt"],
                    "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                },
            ),
        )

        result = self.run_ringer(manifest)

        self.assertEqual(result.returncode, 0, result.stdout)
        rows = self.read_rows()
        self.assertEqual(rows[0]["worker_tokens"], 1234)

    def test_worktree_pass_removes_task_worktree_but_keeps_logs(self) -> None:
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (repo / "README.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.txt"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Ringer Test",
                "-c",
                "user.email=ringer-test@example.invalid",
                "commit",
                "-m",
                "base",
            ],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        workdir = self.root / "work-worktree"
        manifest = self.write_manifest(
            "worktree",
            {
                "run_name": "worktree",
                "workdir": str(workdir),
                "max_parallel": 1,
                "worktrees": True,
                "repo": str(repo),
                "tasks": [
                    {
                        "key": "wt-pass",
                        "engine": "write_done",
                        "spec": "Write done.",
                        "expect_files": ["out.txt"],
                        "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                    }
                ],
            },
        )

        result = self.run_ringer(manifest)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((workdir / "wt-pass").exists())
        self.assertTrue((workdir / "logs" / "wt-pass.worker.log").is_file())
        self.assertEqual([row["verdict"] for row in self.read_rows()], ["PASS"])

    def test_worktree_prepare_failure_logs_error_row(self) -> None:
        repo = self.root / "repo-prepare"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (repo / "README.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.txt"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Ringer Test",
                "-c",
                "user.email=ringer-test@example.invalid",
                "commit",
                "-m",
                "base",
            ],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        workdir = self.root / "work-prepare"
        (workdir / "exists").mkdir(parents=True)
        manifest = self.write_manifest(
            "prepare-failure",
            {
                "run_name": "prepare-failure",
                "workdir": str(workdir),
                "max_parallel": 1,
                "worktrees": True,
                "repo": str(repo),
                "tasks": [
                    {
                        "key": "exists",
                        "engine": "write_done",
                        "spec": "Cannot prepare.",
                        "expect_files": ["out.txt"],
                        "check": "true",
                    }
                ],
            },
        )

        result = self.run_ringer(manifest)

        self.assertEqual(result.returncode, 1, result.stdout)
        rows = self.read_rows()
        self.assertEqual(rows[0]["verdict"], "ERROR")
        self.assertIn("worktree taskdir already exists", rows[0]["notes"])

    def test_task_key_cannot_escape_workdir(self) -> None:
        manifest = self.write_manifest(
            "escape",
            self.manifest(
                "escape",
                {
                    "key": "../escape",
                    "engine": "write_done",
                    "spec": "Escape.",
                    "check": "true",
                },
            ),
        )

        result = self.run_ringer(manifest)

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("task key escapes workdir", result.stdout)

    def test_worktree_task_key_cannot_collide_with_reserved_logs_dir(self) -> None:
        manifest = self.write_manifest(
            "logs-collision",
            self.manifest(
                "logs-collision",
                {
                    "key": "logs/bad",
                    "engine": "write_done",
                    "spec": "Collide.",
                    "check": "true",
                },
                worktrees=True,
                repo=str(self.root),
            ),
        )

        result = self.run_ringer(manifest)

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("reserved worktree logs directory", result.stdout)

    def test_final_state_file_is_finished_after_passing_run(self) -> None:
        # The per-run dashboard this test originally exercised was replaced by
        # the persistent Ringside hud; the surviving contract is the state
        # file: a completed run must land finished with the right summary.
        self.write_config({"slow": ["-c", "sleep 1; printf done > out.txt"]})
        manifest = self.write_manifest(
            "dashboard",
            self.manifest(
                "dashboard",
                {
                    "key": "slow",
                    "engine": "slow",
                    "spec": "Slow enough to serve state.",
                    "expect_files": ["out.txt"],
                    "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                },
            ),
        )

        result = self.run_ringer(manifest, timeout=10)

        self.assertEqual(result.returncode, 0, result.stdout)
        state = self.read_final_state()
        self.assertTrue(state["finished"])
        self.assertEqual(state["state"], "finished")
        self.assertEqual(state["summary"]["pass"], 1)


    def test_check_timeout_is_reported_separately_from_worker_timeout(self) -> None:
        original_timeout = ringer.CHECK_TIMEOUT_S
        ringer.CHECK_TIMEOUT_S = 1
        with tempfile.TemporaryDirectory(prefix="ringer-check-timeout-") as tmp:
            try:
                returncode, timed_out, output = asyncio.run(
                    ringer.Verifier._run_check("sleep 5", Path(tmp))
                )
            finally:
                ringer.CHECK_TIMEOUT_S = original_timeout

        self.assertTrue(timed_out)
        self.assertNotEqual(returncode, 0)
        self.assertIn("[ringer.py] check timed out after 1s", output)

    def test_token_count_parser_accepts_colon_and_newline_formats(self) -> None:
        self.assertEqual(ringer.parse_token_count("tokens used: 1,234", r"tokens\s+used\s*:?\s*([0-9][0-9,]*)"), 1234)
        self.assertEqual(ringer.parse_token_count("tokens used\n5,678", r"tokens\s+used\s*:?\s*([0-9][0-9,]*)"), 5678)


if __name__ == "__main__":
    unittest.main()
