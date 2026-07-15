#!/usr/bin/env python3
"""Per-engine allow_full_access override (task #43).

Covers: config parsing of engines.<name>.allow_full_access, the
full_access_permitted() decision function, and both real enforcement sites
(RingerRunner._run_worker and the dry_run() preview) so the CLI-visible
behavior and the actual spawn-gating behavior can't drift apart.
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    Manifest,
    RingerRunner,
    dry_run,
    full_access_permitted,
    load_engines,
)


def mock_engine(name: str = "mock", *, allow_full_access: bool = False) -> EngineConfig:
    return EngineConfig(
        name=name,
        bin=sys.executable,
        args_template=(str(ROOT / "engines" / "mock_worker.py"), "{spec}"),
        full_access_args=(),
        sandbox_args=(),
        token_regex=None,
        allow_full_access=allow_full_access,
    )


def make_config(root: Path, engines: dict[str, EngineConfig], *, allow_full_access: bool) -> AppConfig:
    return AppConfig(
        path=None,
        identity_default=None,
        state_dir=root / "state",
        dashboard_port_base=8787,
        hud_port=8700,
        hud_app_path=None,
        allow_full_access=allow_full_access,
        eval=EvalConfig(backend="jsonl", jsonl_path=root / "eval.jsonl"),
        engines=engines,
        artifact=ArtifactConfig(
            enabled=False,
            out_template=str(root / "live.html"),
            report_template=str(root / "report.html"),
            index_out=root / "index.html",
        ),
    )


class ConfigParsingTests(unittest.TestCase):
    """engines.<name>.allow_full_access parses like every other per-engine field."""

    def test_default_is_false_when_unset(self) -> None:
        engines = load_engines(
            {"harness": {"bin": "/usr/local/bin/harness", "args_template": ["{spec}"]}}
        )
        self.assertFalse(engines["harness"].allow_full_access)

    def test_explicit_true_is_read(self) -> None:
        engines = load_engines(
            {
                "claude": {
                    "bin": "/usr/local/bin/claude-sandboxed.sh",
                    "args_template": ["{taskdir}", "{access_args}", "{spec}"],
                    "allow_full_access": True,
                }
            }
        )
        self.assertTrue(engines["claude"].allow_full_access)

    def test_explicit_false_is_read(self) -> None:
        engines = load_engines(
            {"codex": {"bin": "/usr/local/bin/codex", "allow_full_access": False}}
        )
        self.assertFalse(engines["codex"].allow_full_access)

    def test_built_in_codex_engine_defaults_to_false(self) -> None:
        engines = load_engines(None)
        self.assertFalse(engines["codex"].allow_full_access)

    def test_only_the_configured_engine_is_affected(self) -> None:
        engines = load_engines(
            {
                "claude": {
                    "bin": "/usr/local/bin/claude-sandboxed.sh",
                    "args_template": ["{taskdir}", "{access_args}", "{spec}"],
                    "allow_full_access": True,
                },
                "grok": {
                    "bin": "/usr/local/bin/grok",
                    "args_template": ["{taskdir}", "{access_args}", "{spec}"],
                },
            }
        )
        self.assertTrue(engines["claude"].allow_full_access)
        self.assertFalse(engines["grok"].allow_full_access)
        self.assertFalse(engines["codex"].allow_full_access)  # untouched built-in default

    def test_overriding_the_built_in_codex_engine_can_opt_it_in_too(self) -> None:
        # "codex" is pre-seeded via built_in_codex_engine() before user config is
        # applied (base is not None here), so this exercises the
        # base.allow_full_access-fallback branch, not just the brand-new-engine path.
        engines = load_engines(
            {"codex": {"bin": "/usr/local/bin/codex", "allow_full_access": True}}
        )
        self.assertTrue(engines["codex"].allow_full_access)

    def test_overriding_codex_bin_without_the_key_keeps_the_false_base_default(self) -> None:
        engines = load_engines({"codex": {"bin": "/usr/local/bin/codex"}})
        self.assertFalse(engines["codex"].allow_full_access)


class FullAccessPermittedTests(unittest.TestCase):
    """Unit tests for the decision function both enforcement sites call."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_global_off_engine_off_denied(self) -> None:
        engine = mock_engine(allow_full_access=False)
        config = make_config(self.root, {"mock": engine}, allow_full_access=False)
        self.assertFalse(full_access_permitted(config, engine))

    def test_global_off_engine_on_allowed(self) -> None:
        engine = mock_engine(allow_full_access=True)
        config = make_config(self.root, {"mock": engine}, allow_full_access=False)
        self.assertTrue(full_access_permitted(config, engine))

    def test_global_off_engine_on_does_not_leak_to_a_different_engine(self) -> None:
        claude = mock_engine("claude", allow_full_access=True)
        codex = mock_engine("codex", allow_full_access=False)
        config = make_config(
            self.root, {"claude": claude, "codex": codex}, allow_full_access=False
        )
        self.assertTrue(full_access_permitted(config, claude))
        self.assertFalse(full_access_permitted(config, codex))

    def test_global_on_allowed_regardless_of_engine_back_compat(self) -> None:
        engine = mock_engine(allow_full_access=False)
        config = make_config(self.root, {"mock": engine}, allow_full_access=True)
        self.assertTrue(full_access_permitted(config, engine))

    def test_unknown_engine_is_denied_unless_global_is_on(self) -> None:
        config = make_config(self.root, {}, allow_full_access=False)
        self.assertFalse(full_access_permitted(config, None))
        config_global_on = make_config(self.root, {}, allow_full_access=True)
        self.assertTrue(full_access_permitted(config_global_on, None))


class RingerRunnerEnforcementTests(unittest.IsolatedAsyncioTestCase):
    """_run_worker is where a task actually gets spawned or blocked."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _runner(self, config: AppConfig, *, engine: str) -> RingerRunner:
        manifest = Manifest.from_obj(
            {
                "run_name": "full-access-scope",
                "workdir": str(self.root / "work"),
                "tasks": [
                    {
                        "key": "task-a",
                        "engine": engine,
                        "spec": "MOCK_FILE: result.txt\nhello\nMOCK_END",
                        "check": "true",
                        "full_access": True,
                    }
                ],
            }
        )
        runner = RingerRunner(manifest, config, "test", dashboard_enabled=False)
        runner.runtimes[0].taskdir.mkdir(parents=True, exist_ok=True)
        return runner

    async def test_global_off_engine_off_task_is_denied(self) -> None:
        engine = mock_engine(allow_full_access=False)
        config = make_config(self.root, {"mock": engine}, allow_full_access=False)
        runner = self._runner(config, engine="mock")
        runtime = runner.runtimes[0]

        result = await runner._run_worker(runtime, runtime.task.spec, 1)

        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("allow_full_access is false", result.error)
        self.assertFalse((runtime.taskdir / "result.txt").exists())

    async def test_global_off_engine_on_task_runs(self) -> None:
        engine = mock_engine(allow_full_access=True)
        config = make_config(self.root, {"mock": engine}, allow_full_access=False)
        runner = self._runner(config, engine="mock")
        runtime = runner.runtimes[0]

        result = await runner._run_worker(runtime, runtime.task.spec, 1)

        self.assertIsNone(result.error)
        self.assertEqual(0, result.returncode)
        self.assertTrue((runtime.taskdir / "result.txt").exists())

    async def test_global_off_engine_on_does_not_unblock_a_different_engine(self) -> None:
        # The whole point of task #43: opting claude in must not also unblock
        # codex/grok/whatever else shares the same config.
        claude = mock_engine("claude", allow_full_access=True)
        other = mock_engine("other", allow_full_access=False)
        config = make_config(
            self.root, {"claude": claude, "other": other}, allow_full_access=False
        )
        runner = self._runner(config, engine="other")
        runtime = runner.runtimes[0]

        result = await runner._run_worker(runtime, runtime.task.spec, 1)

        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("allow_full_access is false", result.error)

    async def test_global_on_task_runs_back_compat(self) -> None:
        engine = mock_engine(allow_full_access=False)
        config = make_config(self.root, {"mock": engine}, allow_full_access=True)
        runner = self._runner(config, engine="mock")
        runtime = runner.runtimes[0]

        result = await runner._run_worker(runtime, runtime.task.spec, 1)

        self.assertIsNone(result.error)
        self.assertEqual(0, result.returncode)
        self.assertTrue((runtime.taskdir / "result.txt").exists())


class DryRunPreviewTests(unittest.TestCase):
    """dry_run() is what a human reads before trusting the gate; it must agree
    with what _run_worker will actually do."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _manifest(self, engine: str) -> Manifest:
        return Manifest.from_obj(
            {
                "run_name": "dry-run-scope",
                "workdir": str(self.root / "work"),
                "tasks": [
                    {
                        "key": "task-a",
                        "engine": engine,
                        "spec": "hello",
                        "check": "true",
                        "full_access": True,
                    }
                ],
            }
        )

    def _render(self, config: AppConfig, engine: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            dry_run(self._manifest(engine), config, "test", False, False)
        return out.getvalue()

    def test_global_off_engine_off_shows_denied(self) -> None:
        engine = mock_engine(allow_full_access=False)
        config = make_config(self.root, {"mock": engine}, allow_full_access=False)
        output = self._render(config, "mock")
        self.assertIn("full_access: true allowed=False", output)
        self.assertIn("command: ERROR full_access requires", output)

    def test_global_off_engine_on_shows_allowed(self) -> None:
        engine = mock_engine(allow_full_access=True)
        config = make_config(self.root, {"mock": engine}, allow_full_access=False)
        output = self._render(config, "mock")
        self.assertIn("full_access: true allowed=True", output)
        self.assertNotIn("command: ERROR", output)

    def test_global_on_shows_allowed(self) -> None:
        engine = mock_engine(allow_full_access=False)
        config = make_config(self.root, {"mock": engine}, allow_full_access=True)
        output = self._render(config, "mock")
        self.assertIn("full_access: true allowed=True", output)
        self.assertNotIn("command: ERROR", output)


if __name__ == "__main__":
    unittest.main()
