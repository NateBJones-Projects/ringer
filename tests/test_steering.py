#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


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
    SteeringConfig,
    SteeringProfile,
    SteeringRule,
    TaskSpec,
    VerifyResult,
    WorkerResult,
    inject_steering_spec,
    load_steering_config,
    parse_steering_profile,
    print_steering_notes,
    resolve_steering_profile,
    steering_profile_candidates,
)


FIXTURE_PROFILE = """---
kind: steering-profile
format: 1
model: openrouter/z-ai/glm-5.2
profile_version: 1.2.3
---

# Steering profile

## R1 · keep-checks-executable

```yaml
id: keep-checks-executable
status: confirmed
audience: worker
```

**Inject:** Keep verification executable and run it before reporting completion.

**Detail:** This is supporting prose.

## R2 · inspect-first

```yaml
id: inspect-first
status: candidate
audience: worker
```

**Inject:** Inspect the existing implementation before editing, then follow its
local patterns.

## R3 · reverify-version

```yaml
id: reverify-version
status: stale-pending-reverify
audience: worker
```

**Inject:** Recheck version-sensitive behavior against the installed tool.

## R4 · discarded-rule

```yaml
id: discarded-rule
status: refuted
audience: worker
```

**Inject:** Apply the discarded approach.

## R5 · driver-brief

```yaml
id: driver-brief
status: confirmed
```

**Inject:** Put the acceptance criteria in the task brief.

## R6 · malformed-rule

```yaml
id: malformed-rule
status: unknown
audience: worker
```

**Inject:** This rule should be skipped.
"""


def toml_string(value: object) -> str:
    return json.dumps(str(value))


def write_profile(path: Path, *, model: str, version: str = "1.0.0") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        FIXTURE_PROFILE.replace("openrouter/z-ai/glm-5.2", model).replace(
            "profile_version: 1.2.3", f"profile_version: {version}"
        ),
        encoding="utf-8",
    )


def test_engine(model_default: str = "openrouter/z-ai/glm-5.2") -> EngineConfig:
    return EngineConfig(
        name="mock",
        bin=sys.executable,
        args_template=(str(ROOT / "engines" / "mock_worker.py"), "{spec}"),
        full_access_args=(),
        sandbox_args=(),
        token_regex=None,
        model_default=model_default,
    )


def make_config(root: Path, steering_dir: Path) -> AppConfig:
    return AppConfig(
        path=None,
        identity_default=None,
        state_dir=root / "state",
        dashboard_port_base=8787,
        hud_port=8700,
        hud_app_path=None,
        allow_full_access=False,
        eval=EvalConfig(backend="jsonl", jsonl_path=root / "eval.jsonl"),
        engines={"mock": test_engine()},
        artifact=ArtifactConfig(
            enabled=False,
            out_template=str(root / "live.html"),
            report_template=str(root / "report.html"),
            index_out=root / "index.html",
        ),
        steering=SteeringConfig(dir=steering_dir),
    )


class SteeringProfileParserTests(unittest.TestCase):
    def test_parser_reads_frontmatter_rules_statuses_audiences_and_inject_paragraphs(self) -> None:
        profile = parse_steering_profile(FIXTURE_PROFILE, slug="glm-5.2")

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual("openrouter/z-ai/glm-5.2", profile.model)
        self.assertEqual("1.2.3", profile.profile_version)
        self.assertEqual("glm-5.2", profile.slug)
        self.assertEqual(
            [
                "keep-checks-executable",
                "inspect-first",
                "reverify-version",
                "discarded-rule",
                "driver-brief",
            ],
            [rule.id for rule in profile.rules],
        )
        self.assertEqual(
            {"confirmed", "candidate", "stale-pending-reverify", "refuted"},
            {rule.status for rule in profile.rules},
        )
        self.assertEqual({"driver", "worker"}, {rule.audience for rule in profile.rules})
        self.assertEqual("driver", profile.rules[-1].audience)
        self.assertEqual(
            "Inspect the existing implementation before editing, then follow its local patterns.",
            profile.rules[1].inject,
        )

    def test_malformed_or_empty_profile_returns_none(self) -> None:
        self.assertIsNone(parse_steering_profile(""))
        self.assertIsNone(parse_steering_profile("---\nmodel: x\n---\n"))
        self.assertIsNone(
            parse_steering_profile(
                "---\nmodel: x\nprofile_version: 1.0.0\n---\n\n# No rules\n"
            )
        )
        self.assertIsNone(parse_steering_profile("not frontmatter"))


class SteeringResolutionTests(unittest.TestCase):
    def test_candidates_try_fully_qualified_slug_before_basename(self) -> None:
        root = Path("/tmp/steering")
        self.assertEqual(
            (
                root / "profiles" / "openrouter-z-ai-glm-5.2.md",
                root / "profiles" / "glm-5.2.md",
            ),
            steering_profile_candidates(root, "openrouter/z-ai/glm-5.2"),
        )

        with tempfile.TemporaryDirectory() as temp_root:
            steering_dir = Path(temp_root)
            full = steering_dir / "profiles" / "openrouter-z-ai-glm-5.2.md"
            basename = steering_dir / "profiles" / "glm-5.2.md"
            write_profile(full, model="full-match", version="2.0.0")
            write_profile(basename, model="basename-match", version="3.0.0")

            profile = resolve_steering_profile(steering_dir, "openrouter/z-ai/glm-5.2")
            self.assertIsNotNone(profile)
            assert profile is not None
            self.assertEqual("full-match", profile.model)

            full.unlink()
            profile = resolve_steering_profile(steering_dir, "openrouter/z-ai/glm-5.2")
            self.assertIsNotNone(profile)
            assert profile is not None
            self.assertEqual("basename-match", profile.model)

    def test_first_existing_malformed_file_wins_without_falling_through(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            steering_dir = Path(temp_root)
            full = steering_dir / "profiles" / "openrouter-z-ai-glm-5.2.md"
            basename = steering_dir / "profiles" / "glm-5.2.md"
            full.parent.mkdir(parents=True)
            full.write_text("malformed", encoding="utf-8")
            write_profile(basename, model="should-not-load")
            self.assertIsNone(
                resolve_steering_profile(steering_dir, "openrouter/z-ai/glm-5.2")
            )


class SteeringInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        parsed = parse_steering_profile(FIXTURE_PROFILE, slug="glm-5.2")
        assert parsed is not None
        self.profile = parsed

    def test_worker_injection_contract(self) -> None:
        original = "Build the requested artifact."
        injected, rule_ids = inject_steering_spec(original, self.profile)

        self.assertTrue(
            injected.startswith(
                "[Steering profile openrouter/z-ai/glm-5.2 v1.2.3 — auto-injected by ringer.py]\n"
            )
        )
        self.assertIn("- Keep verification executable", injected)
        self.assertIn("- (candidate) Inspect the existing implementation", injected)
        self.assertIn(
            "- (unverified on current model version) Recheck version-sensitive behavior",
            injected,
        )
        self.assertNotIn("Apply the discarded approach", injected)
        self.assertNotIn("Put the acceptance criteria", injected)
        self.assertTrue(injected.endswith("[End steering profile]\n\n" + original))
        self.assertEqual(
            ("keep-checks-executable", "inspect-first", "reverify-version"),
            rule_ids,
        )

    def test_candidates_can_be_disabled(self) -> None:
        injected, rule_ids = inject_steering_spec(
            "Original", self.profile, inject_candidates=False
        )
        self.assertNotIn("(candidate)", injected)
        self.assertEqual(("keep-checks-executable", "reverify-version"), rule_ids)

    def test_only_driver_rules_produces_no_block(self) -> None:
        driver_profile = SteeringProfile(
            model="x",
            profile_version="1",
            slug="x",
            rules=(SteeringRule("driver", "confirmed", "driver", "Guide the driver."),),
        )
        self.assertEqual(("Original", ()), inject_steering_spec("Original", driver_profile))

    def test_fail_open_inputs_leave_spec_unchanged(self) -> None:
        original = "Original spec bytes\n"
        self.assertEqual((original, ()), inject_steering_spec(original, None))
        self.assertIsNone(resolve_steering_profile(None, "model"))
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            self.assertIsNone(resolve_steering_profile(root / "missing", "model"))
            malformed = root / "profiles" / "model.md"
            malformed.parent.mkdir()
            malformed.write_text("---\nprofile_version: nope\n---", encoding="utf-8")
            profile = resolve_steering_profile(root, "model")
            self.assertIsNone(profile)
            self.assertEqual((original, ()), inject_steering_spec(original, profile))


class SteeringConfigAndDriverTests(unittest.TestCase):
    def test_env_directory_overrides_config_and_expands_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            home = Path(temp_root)
            with mock.patch.dict(
                os.environ,
                {"HOME": str(home), "RINGER_STEERING_DIR": "~/env-steering"},
                clear=True,
            ):
                config = load_steering_config(
                    {"dir": str(home / "config-steering"), "inject_candidates": False}
                )
        self.assertEqual((home / "env-steering").resolve(), config.dir)
        self.assertFalse(config.inject_candidates)

    def test_config_load_is_fail_open_if_steering_loader_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            config_path = root / "config.toml"
            config_path.write_text(
                f"state_dir = {toml_string(root / 'state')}\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                ringer,
                "load_steering_config",
                side_effect=RuntimeError("broken steering loader"),
            ):
                config = AppConfig.load(config_path)

        self.assertEqual(SteeringConfig(), config.steering)

    def test_driver_rules_print_once_per_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            steering_dir = root / "steering"
            write_profile(
                steering_dir / "profiles" / "openrouter-z-ai-glm-5.2.md",
                model="openrouter/z-ai/glm-5.2",
            )
            manifest = Manifest.from_obj(
                {
                    "run_name": "driver-notes",
                    "workdir": str(root / "work"),
                    "tasks": [
                        {"key": "one", "engine": "mock", "spec": "one", "check": "true"},
                        {"key": "two", "engine": "mock", "spec": "two", "check": "true"},
                    ],
                }
            )
            config = make_config(root, steering_dir)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                print_steering_notes(manifest, config)
            text = output.getvalue()
            self.assertEqual(1, text.count("Steering notes for openrouter/z-ai/glm-5.2"))
            self.assertIn("- (confirmed) Put the acceptance criteria", text)
            self.assertNotIn("Keep verification executable", text)

    def test_driver_notes_use_explicit_model_without_a_known_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            steering_dir = root / "steering"
            write_profile(
                steering_dir / "profiles" / "explicit-model.md",
                model="explicit/model",
            )
            manifest = Manifest.from_obj(
                {
                    "run_name": "driver-notes-explicit-model",
                    "workdir": str(root / "work"),
                    "tasks": [
                        {
                            "key": "one",
                            "engine": "not-configured",
                            "model": "explicit/model",
                            "spec": "one",
                            "check": "true",
                        }
                    ],
                }
            )
            config = make_config(root, steering_dir)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                print_steering_notes(manifest, config)

        self.assertIn("Steering notes for explicit/model", output.getvalue())

class SteeringObservationTests(unittest.TestCase):
    def test_observation_row_schema_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            steering_dir = root / "steering"
            manifest = Manifest.from_obj(
                {
                    "run_name": "observation-test",
                    "workdir": str(root / "work"),
                    "tasks": [
                        {
                            "key": "task-a",
                            "engine": "mock",
                            "task_type": "code-feature",
                            "spec": "Do the work",
                            "check": "true",
                        }
                    ],
                }
            )
            config = make_config(root, steering_dir)
            runner = RingerRunner(manifest, config, "test", dashboard_enabled=False)
            runtime = runner.runtimes[0]
            runtime.attempts = 2
            runtime.steering = {
                "profile": "glm-5.2",
                "version": "1.2.3",
                "rule_ids": ["keep-checks-executable"],
            }

            runner._write_steering_observation(
                runtime,
                resolved_model="openrouter/z-ai/glm-5.2",
                retrying=True,
                worker=WorkerResult(returncode=0, timed_out=False, tokens=42),
                verify=VerifyResult(
                    ok=True,
                    check_returncode=0,
                    check_timed_out=False,
                    raw_output_excerpt="x" * 700,
                ),
                verdict="PASS",
                duration_ms=123,
            )

            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = steering_dir / "observations" / "ringer" / f"{date}.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "ts",
                    "source",
                    "run_id",
                    "run_name",
                    "task_key",
                    "task_type",
                    "engine",
                    "model",
                    "profile",
                    "profile_version",
                    "rules_injected",
                    "attempt",
                    "retry",
                    "verdict",
                    "duration_ms",
                    "worker_tokens",
                    "check_excerpt",
                },
                set(row),
            )
            self.assertEqual("ringer.py", row["source"])
            self.assertEqual("observation-test", row["run_name"])
            self.assertEqual(["keep-checks-executable"], row["rules_injected"])
            self.assertEqual(2, row["attempt"])
            self.assertTrue(row["retry"])
            self.assertEqual(500, len(row["check_excerpt"]))

    def test_observation_write_failure_is_logged_and_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            blocked = root / "not-a-directory"
            blocked.write_text("blocked", encoding="utf-8")
            manifest = Manifest.from_obj(
                {
                    "run_name": "write-failure",
                    "workdir": str(root / "work"),
                    "tasks": [
                        {"key": "task-a", "engine": "mock", "spec": "Do it", "check": "true"}
                    ],
                }
            )
            config = make_config(root, blocked)
            runner = RingerRunner(manifest, config, "test", dashboard_enabled=False)
            runtime = runner.runtimes[0]
            runtime.log_path.parent.mkdir(parents=True)
            runner._write_steering_observation(
                runtime,
                resolved_model="openrouter/z-ai/glm-5.2",
                retrying=False,
                worker=WorkerResult(returncode=0, timed_out=False, tokens=None),
                verify=VerifyResult(True, 0, False, "ok"),
                verdict="PASS",
                duration_ms=1,
            )
            self.assertIn(
                "[ringer.py] steering: observation write failed",
                runtime.log_path.read_text(encoding="utf-8"),
            )


class SteeringIntegrationFailOpenTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_reinjects_profile_and_writes_a_second_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            steering_dir = root / "steering"
            write_profile(
                steering_dir / "profiles" / "openrouter-z-ai-glm-5.2.md",
                model="openrouter/z-ai/glm-5.2",
            )
            manifest = Manifest.from_obj(
                {
                    "run_name": "steering-retry",
                    "workdir": str(root / "work"),
                    "tasks": [
                        {
                            "key": "task-a",
                            "engine": "mock",
                            "spec": "MOCK_FAIL",
                            "check": "false",
                        }
                    ],
                }
            )
            runner = RingerRunner(
                manifest,
                make_config(root, steering_dir),
                "test",
                dashboard_enabled=False,
            )
            runtime = runner.runtimes[0]
            injected_specs: list[str] = []
            original_build_worker_command = ringer.build_worker_command

            def recording_build_worker_command(*args: object, **kwargs: object) -> list[str]:
                spec = kwargs.get("spec")
                if isinstance(spec, str) and spec.startswith("[Steering profile "):
                    injected_specs.append(spec)
                return original_build_worker_command(*args, **kwargs)  # type: ignore[arg-type]

            with mock.patch.object(
                ringer,
                "build_worker_command",
                side_effect=recording_build_worker_command,
            ):
                await runner._run_task(runtime)

            self.assertEqual(2, len(injected_specs))
            self.assertTrue(
                all(
                    spec.startswith("[Steering profile openrouter/z-ai/glm-5.2")
                    for spec in injected_specs
                )
            )
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            observation_path = (
                steering_dir / "observations" / "ringer" / f"{date}.jsonl"
            )
            rows = [
                json.loads(line)
                for line in observation_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([1, 2], [row["attempt"] for row in rows])
            self.assertEqual([False, True], [row["retry"] for row in rows])
            self.assertEqual(
                [
                    "keep-checks-executable",
                    "inspect-first",
                    "reverify-version",
                ],
                rows[1]["rules_injected"],
            )

    async def test_worker_resolution_exception_runs_original_spec(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manifest = Manifest.from_obj(
                {
                    "run_name": "fail-open-worker",
                    "workdir": str(root / "work"),
                    "tasks": [
                        {
                            "key": "task-a",
                            "engine": "mock",
                            "spec": "MOCK_FILE: result.txt\noriginal spec\nMOCK_END",
                            "check": "true",
                        }
                    ],
                }
            )
            runner = RingerRunner(
                manifest,
                make_config(root, root / "steering"),
                "test",
                dashboard_enabled=False,
            )
            runtime = runner.runtimes[0]
            runtime.taskdir.mkdir(parents=True)

            with mock.patch.object(
                ringer,
                "resolve_steering_profile",
                side_effect=RuntimeError("steering exploded"),
            ):
                result = await runner._run_worker(runtime, runtime.task.spec, 1)

            self.assertEqual(0, result.returncode)
            self.assertEqual(
                "original spec\n",
                (runtime.taskdir / "result.txt").read_text(encoding="utf-8"),
            )
            log = runtime.log_path.read_text(encoding="utf-8")
            self.assertNotIn("[Steering profile", log)
            self.assertIn("[ringer.py] steering: no profile matched", log)
            self.assertEqual(
                {"profile": None, "version": None, "rule_ids": []},
                runtime.steering,
            )

    async def test_observation_hook_exception_does_not_escape_attempt_logging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manifest = Manifest.from_obj(
                {
                    "run_name": "fail-open-observation-hook",
                    "workdir": str(root / "work"),
                    "tasks": [
                        {
                            "key": "task-a",
                            "engine": "mock",
                            "spec": "Do it",
                            "check": "true",
                        }
                    ],
                }
            )
            runner = RingerRunner(
                manifest,
                make_config(root, root / "steering"),
                "test",
                dashboard_enabled=False,
            )
            runtime = runner.runtimes[0]
            runtime.attempts = 1
            runtime.last_worker_command = ["mock", "--model=mock/model"]

            with mock.patch.object(
                runner,
                "_write_steering_observation",
                side_effect=RuntimeError("observation hook exploded"),
            ):
                runner._log_attempt(
                    runtime,
                    runtime.task.spec,
                    False,
                    WorkerResult(returncode=0, timed_out=False, tokens=None),
                    VerifyResult(True, 0, False, "ok"),
                    "PASS",
                    1,
                )

            rows = [
                json.loads(line)
                for line in (root / "eval.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(1, len(rows))
            self.assertEqual("PASS", rows[0]["verdict"])


class SteeringMockEngineFunctionalTests(unittest.TestCase):
    def test_mock_worker_receives_injected_spec_and_observation_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            ringer_home = root / "ringer-home"
            state_dir = root / "state"
            steering_dir = root / "steering"
            workdir = root / "work"
            config_path = root / "config.toml"
            manifest_path = root / "manifest.json"
            home.mkdir()
            ringer_home.mkdir()
            write_profile(
                steering_dir / "profiles" / "mock-model.md",
                model="mock/model",
                version="4.5.6",
            )

            config_path.write_text(
                "\n".join(
                    [
                        f"state_dir = {toml_string(state_dir)}",
                        "",
                        "[steering]",
                        f"dir = {toml_string(steering_dir)}",
                        "inject_candidates = true",
                        "",
                        "[eval]",
                        'backend = "jsonl"',
                        f"jsonl_path = {toml_string(root / 'runs.jsonl')}",
                        "",
                        "[artifact]",
                        "enabled = false",
                        "",
                        "[engines.mock]",
                        f"bin = {toml_string(sys.executable)}",
                        'model_default = "mock/model"',
                        "args_template = [",
                        f"  {toml_string(ROOT / 'engines' / 'mock_worker.py')},",
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
                        "run_name": "steering-functional",
                        "workdir": str(workdir),
                        "tasks": [
                            {
                                "key": "steered-task",
                                "engine": "mock",
                                "task_type": "code-feature",
                                "spec": "MOCK_FILE: result.txt\nsteered run\nMOCK_END",
                                "check": "test \"$(cat result.txt)\" = \"steered run\"",
                                "expect_files": ["result.txt"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "RINGER_HOME": str(ringer_home),
                    "XDG_CONFIG_HOME": str(root / "xdg-config"),
                }
            )
            env.pop("RINGER_STEERING_DIR", None)

            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "run",
                    str(manifest_path),
                    "--config",
                    str(config_path),
                    "--no-dashboard",
                    "--identity",
                    "steering-test",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )

            combined = proc.stdout + proc.stderr
            self.assertEqual(0, proc.returncode, combined)
            log = (workdir / "steered-task" / "worker.log").read_text(encoding="utf-8")
            self.assertIn(
                "[Steering profile mock/model v4.5.6 — auto-injected by ringer.py]",
                log,
            )
            self.assertIn(
                "[ringer.py] steering: profile=mock-model version=4.5.6",
                log,
            )

            state_path = next((state_dir / "runs").glob("*.json"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "profile": "mock-model",
                    "version": "4.5.6",
                    "rule_ids": [
                        "keep-checks-executable",
                        "inspect-first",
                        "reverify-version",
                    ],
                },
                state["tasks"][0]["steering"],
            )

            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            observation_path = (
                steering_dir / "observations" / "ringer" / f"{date}.jsonl"
            )
            rows = [
                json.loads(line)
                for line in observation_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(1, len(rows))
            self.assertEqual("PASS", rows[0]["verdict"])
            self.assertEqual("mock/model", rows[0]["model"])
            self.assertEqual(
                ["keep-checks-executable", "inspect-first", "reverify-version"],
                rows[0]["rules_injected"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
