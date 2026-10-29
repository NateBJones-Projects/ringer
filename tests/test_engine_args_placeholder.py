#!/usr/bin/env python3
"""Per-task engine_args must reach the process, or the run must refuse (BLP-201).

build_worker_command substitutes engine_args only where the resolved
args_template carries the literal {engine_args} token. When it does not, the
arguments are dropped with no error anywhere: manifest lint passes and the flag
simply never reaches the process. Observed on the BLP-189 arc, where the gemini
block had no placeholder and --include-directories vanished silently.

Chris's gemini template now carries the placeholder, so this is regression
protection rather than a live fix.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    Manifest,
    build_worker_command,
    lint_manifest,
    validate_manifest_engines,
)

LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)
GOOD_CHECK = (
    "test -s output.txt && grep -q 'ready' output.txt || "
    "{ echo 'FAIL: output.txt missing or does not contain ready'; exit 1; }"
)
DROPPED_ARGS = ("--include-directories", "/srv/repo")


def engine_without_placeholder() -> EngineConfig:
    return EngineConfig(
        name="gemini",
        bin="/usr/local/bin/gemini",
        args_template=("--yolo", "-m", "{model}", "-p", "{spec}"),
        full_access_args=(),
        sandbox_args=(),
        token_regex=None,
        model_default="gemini-flash-latest",
    )


def engine_with_placeholder() -> EngineConfig:
    return EngineConfig(
        name="gemini",
        bin="/usr/local/bin/gemini",
        args_template=("--yolo", "{engine_args}", "-m", "{model}", "-p", "{spec}"),
        full_access_args=(),
        sandbox_args=(),
        token_regex=None,
        model_default="gemini-flash-latest",
    )


class EngineArgsPlaceholderTests(unittest.TestCase):
    def config(self, engine: EngineConfig) -> AppConfig:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        return AppConfig(
            path=None,
            identity_default=None,
            state_dir=root,
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=root / "eval.jsonl"),
            engines={engine.name: engine},
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(root / "live.html"),
                report_template=str(root / "report.html"),
                index_out=root / "index.html",
            ),
        )

    def manifest(self) -> Manifest:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Manifest.from_obj(
            {
                "run_name": "engine-args-test",
                "workdir": str(Path(temp.name) / "work"),
                "tasks": [
                    {
                        "key": "a",
                        "spec": LONG_SPEC,
                        "check": GOOD_CHECK,
                        "engine": "gemini",
                        "engine_args": list(DROPPED_ARGS),
                        "expect_files": ["output.txt"],
                        "verified": "output exists with expected content",
                    }
                ],
            }
        )

    # --- the drop is real ---------------------------------------------
    def test_args_vanish_from_the_built_command_without_the_placeholder(self) -> None:
        command = build_worker_command(
            engine_without_placeholder(),
            taskdir=Path("/tmp/taskdir"),
            spec="do the thing",
            full_access=False,
            engine_args=DROPPED_ARGS,
        )
        for arg in DROPPED_ARGS:
            self.assertNotIn(arg, command, "this is the bug being guarded against")

    # --- run refuses ---------------------------------------------------
    def test_run_refuses_to_spawn_the_task(self) -> None:
        with self.assertRaises(ValueError) as caught:
            validate_manifest_engines(self.manifest(), self.config(engine_without_placeholder()))
        message = str(caught.exception)
        self.assertIn("a", message, "the finding must name the task")
        self.assertIn("gemini", message, "the finding must name the engine")
        for arg in DROPPED_ARGS:
            self.assertIn(arg, message, "the finding must name the dropped args")

    # --- lint says so, as a failing finding -----------------------------
    def test_lint_emits_a_failing_finding(self) -> None:
        findings = lint_manifest(self.manifest(), config=self.config(engine_without_placeholder()))
        matching = [f for f in findings if "engine_args" in f]
        self.assertTrue(matching, f"expected an engine_args finding, got: {findings}")
        finding = matching[0]
        self.assertTrue(
            finding.startswith("ERROR:"),
            f"a dropped argument is a failure, not a nudge: {finding}",
        )
        self.assertIn("gemini", finding)
        for arg in DROPPED_ARGS:
            self.assertIn(arg, finding)

    # --- the placeholder case stays clean -------------------------------
    def test_placeholder_present_means_no_finding_and_args_in_the_command(self) -> None:
        config = self.config(engine_with_placeholder())
        manifest = self.manifest()

        validate_manifest_engines(manifest, config)  # must not raise

        findings = lint_manifest(manifest, config=config)
        self.assertEqual(
            [], [f for f in findings if "engine_args" in f], "no finding when the args reach the process"
        )

        command = build_worker_command(
            engine_with_placeholder(),
            taskdir=Path("/tmp/taskdir"),
            spec="do the thing",
            full_access=False,
            engine_args=DROPPED_ARGS,
        )
        for arg in DROPPED_ARGS:
            self.assertIn(arg, command)

    def test_no_engine_args_means_no_finding(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        manifest = Manifest.from_obj(
            {
                "run_name": "engine-args-test",
                "workdir": str(Path(temp.name) / "work"),
                "tasks": [
                    {
                        "key": "a",
                        "spec": LONG_SPEC,
                        "check": GOOD_CHECK,
                        "engine": "gemini",
                        "expect_files": ["output.txt"],
                        "verified": "output exists with expected content",
                    }
                ],
            }
        )
        config = self.config(engine_without_placeholder())
        validate_manifest_engines(manifest, config)  # must not raise
        self.assertEqual([], [f for f in lint_manifest(manifest, config=config) if "engine_args" in f])


if __name__ == "__main__":
    unittest.main()
