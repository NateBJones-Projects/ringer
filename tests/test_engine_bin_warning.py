from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402


LONG_SPEC = (
    "Create result.txt in the task directory with a clear success marker, keep the work scoped, "
    "and make any failure easy to diagnose from the check output."
)
GOOD_CHECK = "test -s result.txt || { echo 'missing result.txt'; exit 1; }"
MISSING_BIN = "ringer-definitely-missing-engine-bin"


def toml_string(value: object) -> str:
    return json.dumps(str(value))


class EngineBinWarningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="ringer-engine-bin-")
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "config.toml"
        self.manifest_path = self.root / "manifest.json"
        self.path_dir = self.root / "path"
        self.path_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def engine(self, name: str, bin_value: str) -> ringer.EngineConfig:
        return ringer.EngineConfig(
            name=name,
            bin=bin_value,
            args_template=("{spec}",),
            full_access_args=(),
            sandbox_args=(),
            token_regex=None,
        )

    def write_config(self, engines: dict[str, str]) -> None:
        lines = [
            f"state_dir = {toml_string(self.root / 'state')}",
            "",
            "[eval]",
            f"jsonl_path = {toml_string(self.root / 'runs.jsonl')}",
            "",
        ]
        for name, bin_value in engines.items():
            lines.extend(
                [
                    f"[engines.{name}]",
                    f"bin = {toml_string(bin_value)}",
                    'args_template = ["{spec}"]',
                    "sandbox_args = []",
                    "full_access_args = []",
                    "",
                ]
            )
        self.config_path.write_text("\n".join(lines), encoding="utf-8")

    def write_manifest(self, *, engine: str = "worker", clean: bool = True) -> Path:
        task = {
            "key": "task-one",
            "engine": engine,
            "spec": LONG_SPEC if clean else "too short",
            "check": GOOD_CHECK,
            "expect_files": ["result.txt"],
            "verified": "result.txt exists and is non-empty.",
            "task_type": "code-feature",
        }
        data = {
            "run_name": "engine-bin-warning-test",
            "workdir": str(self.root / "work"),
            "max_parallel": 1,
            "tasks": [task],
        }
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        return self.manifest_path

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"RINGER_NO_SELF_UPDATE": "1", "PATH": str(self.path_dir)},
            clear=False,
        ):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = ringer.main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_pure_diagnostics_are_immutable_and_name_key_value_and_path(self) -> None:
        diagnostics = ringer.collect_engine_bin_diagnostics(
            {"missing": self.engine("missing", MISSING_BIN)},
            path_value=str(self.path_dir),
            path_was_set=True,
        )

        self.assertEqual(1, len(diagnostics))
        diagnostic = diagnostics[0]
        self.assertEqual("engines.missing.bin", diagnostic.config_key)
        self.assertEqual(MISSING_BIN, diagnostic.value)
        self.assertIn("engines.missing.bin", diagnostic.warning())
        self.assertIn(MISSING_BIN, diagnostic.warning())
        self.assertIn(str(self.path_dir), diagnostic.warning())
        with self.assertRaises(FrozenInstanceError):
            diagnostic.value = "changed"  # type: ignore[misc]

    def test_explicit_path_is_quiet_even_when_missing(self) -> None:
        diagnostics = ringer.collect_engine_bin_diagnostics(
            {"explicit": self.engine("explicit", str(self.root / "missing-tool"))},
            path_value=str(self.path_dir),
            path_was_set=True,
        )

        self.assertEqual((), diagnostics)

    @unittest.skipIf(os.name == "nt", "POSIX executable-bit fixture")
    def test_resolvable_bare_name_is_quiet(self) -> None:
        executable = self.path_dir / "ok-engine"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)

        diagnostics = ringer.collect_engine_bin_diagnostics(
            {"ok": self.engine("ok", executable.name)},
            path_value=str(self.path_dir),
            path_was_set=True,
        )

        self.assertEqual((), diagnostics)

    def test_unset_path_diagnostic_distinguishes_unset_from_empty(self) -> None:
        diagnostics = ringer.collect_engine_bin_diagnostics(
            {"missing": self.engine("missing", MISSING_BIN)},
            path_was_set=False,
        )

        self.assertEqual(1, len(diagnostics))
        warning = diagnostics[0].warning()
        self.assertIn("searched PATH: <unset>", warning)
        self.assertNotIn("<empty>", warning)

    def test_lint_prints_warning_but_still_succeeds(self) -> None:
        self.write_config({"missing": MISSING_BIN})
        self.write_manifest(engine="missing", clean=True)

        rc, stdout, stderr = self.run_main(
            ["--config", str(self.config_path), "lint", str(self.manifest_path)]
        )

        self.assertEqual(0, rc)
        self.assertIn("lint: clean", stdout)
        self.assertIn("ringer.py: warning:", stderr)
        self.assertIn("engines.missing.bin", stderr)
        self.assertIn(MISSING_BIN, stderr)

    def test_malformed_config_does_not_change_lint_result(self) -> None:
        self.config_path.write_text("engines = 1\n", encoding="utf-8")
        self.write_manifest(clean=False)

        rc, stdout, stderr = self.run_main(
            ["--config", str(self.config_path), "lint", str(self.manifest_path)]
        )

        self.assertEqual(1, rc)
        self.assertIn("lint: task-one: spec is probably underspecified", stdout)
        self.assertNotIn("config", stderr.lower())

    def test_used_missing_engine_warns_before_fatal_preflight(self) -> None:
        self.write_config({"missing": MISSING_BIN})
        self.write_manifest(engine="missing", clean=True)

        rc, _stdout, stderr = self.run_main(
            [
                "--config",
                str(self.config_path),
                "run",
                str(self.manifest_path),
                "--no-dashboard",
                "--identity",
                "test-runner",
            ]
        )

        self.assertEqual(2, rc)
        warning_index = stderr.index("ringer.py: warning:")
        fatal_index = stderr.index("ringer.py: error: engine 'missing' binary not found")
        self.assertLess(warning_index, fatal_index)

    def test_unused_missing_engine_warns_without_fatal_preflight(self) -> None:
        self.write_config({"worker": sys.executable, "unused": MISSING_BIN})
        self.write_manifest(engine="worker", clean=True)

        rc, stdout, stderr = self.run_main(
            [
                "--config",
                str(self.config_path),
                "run",
                str(self.manifest_path),
                "--no-dashboard",
                "--identity",
                "test-runner",
                "--dry-run",
            ]
        )

        self.assertEqual(0, rc)
        self.assertIn("DRY RUN:", stdout)
        self.assertIn("engines.unused.bin", stderr)
        self.assertNotIn("binary not found", stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
