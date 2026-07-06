#!/usr/bin/env python3
"""Per-task model routing: the {model} placeholder, model_default, validation."""
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
    TaskSpec,
    build_worker_command,
    load_engines,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
