#!/usr/bin/env python3
"""Per-task model routing: the {model} placeholder, model_default, validation."""
from __future__ import annotations

import json
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
    RingerRunner,
    TaskSpec,
    VerifyResult,
    WorkerResult,
    build_worker_command,
    effective_model_from_command,
    load_engines,
    preflight_engine_bins,
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


def harness_engine(model_default: str = "openrouter/z-ai/glm-5.2") -> EngineConfig:
    return EngineConfig(
        name="opencode",
        bin="/usr/local/bin/opencode",
        args_template=("run", "-m", "{model}", "--dir", "{taskdir}", "{spec}"),
        full_access_args=(),
        sandbox_args=(),
        token_regex=None,
        model_default=model_default,
    )


def codex_like_engine() -> EngineConfig:
    return EngineConfig(
        name="codex",
        bin="/usr/local/bin/codex",
        args_template=("exec", "-C", "{taskdir}", "{spec}"),
        full_access_args=(),
        sandbox_args=(),
        token_regex=None,
    )


class EffectiveModelFromCommandTests(unittest.TestCase):
    def test_reads_supported_flag_forms(self) -> None:
        self.assertEqual("gpt-5.6-sol", effective_model_from_command(["codex", "-m", "gpt-5.6-sol"]))
        self.assertEqual(
            "gpt-5.6-luna",
            effective_model_from_command(["codex", "--model", "gpt-5.6-luna"]),
        )
        self.assertEqual(
            "gpt-5.6-terra",
            effective_model_from_command(["codex", "--model=gpt-5.6-terra"]),
        )

    def test_ignores_model_flag_text_inside_task_spec(self) -> None:
        self.assertEqual(
            "",
            effective_model_from_command(["codex", "exec", "Explain why -m is a CLI flag"]),
        )

    def test_returns_empty_when_absent_or_missing_value(self) -> None:
        self.assertEqual("", effective_model_from_command([]))
        self.assertEqual("", effective_model_from_command(["codex", "exec"]))
        self.assertEqual("", effective_model_from_command(["codex", "--model"]))
        self.assertEqual("", effective_model_from_command(["codex", "--model="]))


class ModelPlaceholderTests(unittest.TestCase):
    def test_model_default_fills_placeholder(self) -> None:
        cmd = build_worker_command(
            harness_engine(), taskdir=Path("/tmp/t"), spec="do it", full_access=False
        )
        self.assertEqual("openrouter/z-ai/glm-5.2", cmd[cmd.index("-m") + 1])

    def test_task_model_overrides_default(self) -> None:
        cmd = build_worker_command(
            harness_engine(),
            taskdir=Path("/tmp/t"),
            spec="do it",
            full_access=False,
            model="openrouter/moonshotai/kimi-k2.7-code",
        )
        self.assertEqual("openrouter/moonshotai/kimi-k2.7-code", cmd[cmd.index("-m") + 1])

    def test_model_args_expands_with_resolved_model(self) -> None:
        engine = EngineConfig(
            name="codex",
            bin="codex",
            args_template=("exec", "{access_args}", "{model_args}", "{spec}"),
            full_access_args=(),
            sandbox_args=("--sandbox", "workspace-write"),
            token_regex=None,
            model_default="gpt-5.6-sol",
        )
        cmd = build_worker_command(
            engine, taskdir=Path("/tmp/t"), spec="do it", full_access=False
        )
        self.assertEqual(
            ["codex", "exec", "--sandbox", "workspace-write", "-m", "gpt-5.6-sol", "do it"],
            cmd,
        )

    def test_model_args_expands_to_nothing_without_resolved_model(self) -> None:
        engine = EngineConfig(
            name="codex",
            bin="codex",
            args_template=("exec", "{model_args}", "{spec}"),
            full_access_args=(),
            sandbox_args=(),
            token_regex=None,
        )
        cmd = build_worker_command(
            engine, taskdir=Path("/tmp/t"), spec="do it", full_access=False
        )
        self.assertEqual(["codex", "exec", "do it"], cmd)

    def test_task_spec_parses_and_validates_model(self) -> None:
        task = TaskSpec.from_obj(
            {
                "key": "a",
                "spec": LONG_SPEC,
                "check": GOOD_CHECK,
                "model": "  openrouter/x  ",
            }
        )
        self.assertEqual("openrouter/x", task.model)
        with self.assertRaisesRegex(ValueError, "model must be a string"):
            TaskSpec.from_obj(
                {"key": "a", "spec": LONG_SPEC, "check": GOOD_CHECK, "model": 5}
            )

    def test_load_engines_reads_model_default(self) -> None:
        engines = load_engines(
            {
                "harness": {
                    "bin": "/usr/local/bin/opencode",
                    "args_template": ["run", "-m", "{model}", "{spec}"],
                    "model_default": "openrouter/z-ai/glm-5.2",
                }
            }
        )
        self.assertEqual("openrouter/z-ai/glm-5.2", engines["harness"].model_default)


class ModelValidationTests(unittest.TestCase):
    def config(self, engines: dict[str, EngineConfig]) -> AppConfig:
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
            engines=engines,
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(root / "live.html"),
                report_template=str(root / "report.html"),
                index_out=root / "index.html",
            ),
        )

    def manifest(self, task: dict[str, object]) -> Manifest:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Manifest.from_obj(
            {
                "run_name": "model-test",
                "workdir": str(Path(temp.name) / "work"),
                "tasks": [task],
            }
        )

    def base_task(self, **extra: object) -> dict[str, object]:
        task: dict[str, object] = {
            "key": "a",
            "spec": LONG_SPEC,
            "check": GOOD_CHECK,
            "expect_files": ["output.txt"],
            "verified": "output exists with expected content",
        }
        task.update(extra)
        return task

    def test_model_on_non_harness_engine_is_rejected(self) -> None:
        config = self.config({"codex": codex_like_engine()})
        manifest = self.manifest(self.base_task(engine="codex", model="openrouter/x"))
        with self.assertRaisesRegex(ValueError, "silently ignored"):
            validate_manifest_engines(manifest, config)

    def test_harness_without_any_model_is_rejected(self) -> None:
        config = self.config({"opencode": harness_engine(model_default="")})
        manifest = self.manifest(self.base_task(engine="opencode"))
        with self.assertRaisesRegex(ValueError, "needs a model"):
            validate_manifest_engines(manifest, config)

    def test_harness_with_default_or_task_model_is_accepted(self) -> None:
        config = self.config({"opencode": harness_engine()})
        validate_manifest_engines(self.manifest(self.base_task(engine="opencode")), config)

        config = self.config({"opencode": harness_engine(model_default="")})
        validate_manifest_engines(
            self.manifest(self.base_task(engine="opencode", model="openrouter/x")),
            config,
        )

    def test_model_args_without_a_resolved_model_is_valid(self) -> None:
        engine = EngineConfig(
            name="codex",
            bin="codex",
            args_template=("exec", "{model_args}", "{spec}"),
            full_access_args=(),
            sandbox_args=(),
            token_regex=None,
        )
        config = self.config({"codex": engine})
        manifest = self.manifest(self.base_task(engine="codex"))
        validate_manifest_engines(manifest, config)

    def test_eval_row_falls_back_to_model_in_last_worker_command(self) -> None:
        config = self.config({"codex": codex_like_engine()})
        manifest = self.manifest(self.base_task(engine="codex"))
        runner = RingerRunner(
            manifest,
            config=config,
            identity="tester",
            dashboard_enabled=False,
        )
        runtime = runner.runtimes[0]
        runtime.last_worker_command = ["codex", "exec", "-m", "gpt-5.6-sol", "do it"]
        runner._log_attempt(
            runtime,
            runtime.task.spec,
            False,
            WorkerResult(returncode=0, timed_out=False, tokens=123),
            VerifyResult(ok=True, check_returncode=0, check_timed_out=False, raw_output_excerpt="ok"),
            "PASS",
            456,
        )
        payload = json.loads(config.eval.jsonl_path.read_text(encoding="utf-8"))
        self.assertEqual("gpt-5.6-sol", payload["model"])
        self.assertIn("model=gpt-5.6-sol", payload["notes"])
        state = runner.state_writer.snapshot()
        self.assertEqual("gpt-5.6-sol", state["tasks"][0]["model"])

    def test_preflight_catches_missing_engine_binary(self) -> None:
        broken = EngineConfig(
            name="codex",
            bin="/nonexistent/path/to/codex",
            args_template=("exec", "{spec}"),
            full_access_args=(),
            sandbox_args=(),
            token_regex=None,
        )
        config = self.config({"codex": broken})
        manifest = self.manifest(self.base_task(engine="codex"))
        with self.assertRaisesRegex(ValueError, "binary not found.*npm install -g @openai/codex"):
            preflight_engine_bins(manifest, config)

    def test_preflight_accepts_absolute_and_path_resolved_binaries(self) -> None:
        absolute = EngineConfig(
            name="worker",
            bin=sys.executable,
            args_template=("{spec}",),
            full_access_args=(),
            sandbox_args=(),
            token_regex=None,
        )
        bare = EngineConfig(
            name="shellworker",
            bin="sh",
            args_template=("{spec}",),
            full_access_args=(),
            sandbox_args=(),
            token_regex=None,
        )
        config = self.config({"worker": absolute, "shellworker": bare})
        preflight_engine_bins(self.manifest(self.base_task(engine="worker")), config)
        preflight_engine_bins(self.manifest(self.base_task(engine="shellworker")), config)


if __name__ == "__main__":
    unittest.main(verbosity=2)
