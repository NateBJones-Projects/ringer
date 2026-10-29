#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import Manifest, lint_manifest  # noqa: E402


LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)

GOOD_CHECK = (
    "test -s output.txt && grep -q 'ready' output.txt || "
    "{ echo 'FAIL: output.txt missing or does not contain ready'; exit 1; }"
)


def toml_string(value: object) -> str:
    return json.dumps(str(value))


class LintFootgunTests(unittest.TestCase):
    def lint(
        self,
        *,
        engine: str = "claude",
        check: str = GOOD_CHECK,
        engine_args: list[str] | None = None,
        expect_files: list[str] | None = None,
        worktrees: bool = False,
    ) -> list[str]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        workdir = Path(temp_dir.name) / "work"
        expect = expect_files if expect_files is not None else ["output.txt"]
        # "{task}" stands for this manifest's own task directory.
        expect = [item.replace("{task}", str(workdir / "one")) for item in expect]
        obj: dict[str, object] = {
            "run_name": "footgun-test",
            "workdir": str(workdir),
            "max_parallel": 1,
            "worktrees": worktrees,
            "tasks": [
                {
                    "key": "one",
                    "spec": LONG_SPEC,
                    "check": check,
                    "engine": engine,
                    "engine_args": engine_args or [],
                    "expect_files": expect,
                    "verified": "output.txt says ready",
                }
            ],
        }
        if worktrees:
            obj["repo"] = temp_dir.name
        return lint_manifest(Manifest.from_obj(obj))

    def hits(self, findings: list[str], word: str) -> list[str]:
        return [f for f in findings if word in f]

    # Rule 1
    def test_trailing_list_flag_swallows_spec(self) -> None:
        findings = self.lint(engine_args=["--model", "x", "--allowedTools"])
        hits = self.hits(findings, "swallow")
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].startswith("one: "))
        self.assertIn("--allowedTools", hits[0])
        self.assertIn("--allowedTools=", hits[0])

    def test_list_flag_safe_forms(self) -> None:
        for args in (
            ["--allowedTools=Read,Write"],
            ["--allowedTools", "--effort", "max"],
            ["--effort", "max"],
        ):
            with self.subTest(args=args):
                self.assertEqual(self.hits(self.lint(engine_args=args), "swallow"), [])
        other = self.lint(engine="codex", engine_args=["--tools"])
        self.assertEqual(self.hits(other, "swallow"), [])

    # Rule 2
    def test_unbalanced_quote(self) -> None:
        findings = self.lint(check="echo 'it's broken' && test -s output.txt")
        self.assertEqual(len(self.hits(findings, "unbalanced quote")), 1)

    def test_quoted_heredoc_with_apostrophe_is_not_unbalanced(self) -> None:
        check = "cat <<'EOF' > out.txt\ndon't panic\nEOF\ntest -s output.txt || { echo FAIL; exit 1; }"
        self.assertEqual(self.hits(self.lint(check=check), "unbalanced quote"), [])
        self.assertEqual(self.hits(self.lint(), "unbalanced quote"), [])

    # Rule 3
    def test_codex_outside_task_dir(self) -> None:
        findings = self.lint(engine="codex", expect_files=["/tmp/elsewhere/result.txt"])
        hits = self.hits(findings, "writable_roots")
        self.assertEqual(len(hits), 1)
        self.assertIn("/tmp/elsewhere/result.txt", hits[0])
        self.assertIn("sandbox_workspace_write.writable_roots", hits[0])

    def test_codex_writable_roots_negatives(self) -> None:
        cases = {
            "inside task dir": dict(expect_files=["{task}/sub/result.txt"]),
            "relative": dict(expect_files=["result.txt"]),
            "writable_roots given": dict(
                expect_files=["/tmp/elsewhere/result.txt"],
                engine_args=["-c", 'sandbox_workspace_write.writable_roots=["/tmp/elsewhere"]'],
            ),
            "worktrees mode": dict(expect_files=["/tmp/elsewhere/result.txt"], worktrees=True),
            "other engine": dict(engine="claude", expect_files=["/tmp/elsewhere/result.txt"]),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                kwargs.setdefault("engine", "codex")
                self.assertEqual(self.hits(self.lint(**kwargs), "writable_roots"), [])

    # Rule 4
    def test_git_status_porcelain_collapses_untracked(self) -> None:
        for check in (
            'test -z "$(git status --porcelain)" || { echo FAIL dirty; exit 1; }',
            "git status -s | grep -q . || { echo FAIL; exit 1; }",
        ):
            with self.subTest(check=check):
                self.assertEqual(len(self.hits(self.lint(check=check), "--untracked-files=all")), 1)

    def test_git_status_negatives(self) -> None:
        for check in (
            'test -z "$(git status --porcelain -uall)" || { echo FAIL dirty; exit 1; }',
            'test -z "$(git status --porcelain --untracked-files=all)" || { echo FAIL; exit 1; }',
            'test -z "$(git status --porcelain -u all)" || { echo FAIL; exit 1; }',
            "git status | grep -q clean || { echo FAIL; exit 1; }",
            "# git status --porcelain\ngrep -q x output.txt || { echo FAIL; exit 1; }",
        ):
            with self.subTest(check=check):
                self.assertEqual(self.hits(self.lint(check=check), "--untracked-files=all"), [])

    # Rule 5
    def test_bashisms_under_bin_sh(self) -> None:
        for check in (
            "set -o pipefail; cat output.txt | grep -q ready || { echo FAIL; exit 1; }",
            "[[ -s output.txt ]] || { echo FAIL; exit 1; }",
            'grep -q ready <<< "$(cat output.txt)" || { echo FAIL; exit 1; }',
        ):
            with self.subTest(check=check):
                self.assertEqual(len(self.hits(self.lint(check=check), "/bin/sh")), 1)

    def test_bashisms_inside_bash_c_are_fine(self) -> None:
        for check in (
            "bash -c 'set -o pipefail; [[ -s output.txt ]] || { echo FAIL; exit 1; }'",
            'bash -lc "[[ -s output.txt ]] || { echo FAIL; exit 1; }"',
            "# [[ only in a comment\ntest -s output.txt || { echo FAIL; exit 1; }",
        ):
            with self.subTest(check=check):
                self.assertEqual(self.hits(self.lint(check=check), "/bin/sh"), [])


class RunSwallowTests(unittest.TestCase):
    def run_cli(self, template: list[str]) -> tuple[subprocess.CompletedProcess[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        marker = root / "worker-ran"
        fake = root / "fake_claude.py"
        fake.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('ran')\n"
            "Path('output.txt').write_text('ready\\n')\n",
            encoding="utf-8",
        )
        items = ", ".join(toml_string(str(fake) if item == "FAKE" else item) for item in template)
        config_path = root / "config.toml"
        config_path.write_text(
            "\n".join(
                [
                    f"state_dir = {toml_string(root / 'state')}",
                    "",
                    "[eval]",
                    'backend = "jsonl"',
                    f"jsonl_path = {toml_string(root / 'runs.jsonl')}",
                    "",
                    "[artifact]",
                    "enabled = false",
                    "",
                    "[engines.claude]",
                    f"bin = {toml_string(sys.executable)}",
                    f"args_template = [{items}]",
                    "sandbox_args = []",
                    "full_access_args = []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "run_name": "swallow-test",
                    "workdir": str(root / "work"),
                    "tasks": [
                        {
                            "key": "one",
                            "engine": "claude",
                            "engine_args": ["--allowedTools"],
                            "spec": LONG_SPEC,
                            "check": GOOD_CHECK,
                            "expect_files": ["output.txt"],
                            "verified": "output.txt says ready",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (root / "home").mkdir()
        (root / "ringer-home").mkdir()
        env = os.environ.copy()
        env["RINGER_NO_SELF_UPDATE"] = "1"
        env["HOME"] = str(root / "home")
        env["RINGER_HOME"] = str(root / "ringer-home")
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
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return proc, marker

    def test_run_refuses_when_spec_follows_engine_args(self) -> None:
        proc, marker = self.run_cli(["FAKE", "{engine_args}", "{spec}"])
        output = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertIn("swallow", output)
        self.assertIn("args_template", output)
        self.assertFalse(marker.exists())

    def test_run_proceeds_when_spec_precedes_engine_args(self) -> None:
        proc, marker = self.run_cli(["FAKE", "{spec}", "{engine_args}"])
        output = proc.stdout + proc.stderr
        # The lint warning still prints, but the run is not refused.
        self.assertEqual(proc.returncode, 0, output)
        self.assertTrue(marker.exists(), output)
        self.assertIn("PASS", output)


if __name__ == "__main__":
    unittest.main()
