from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402


def engine(name: str, *, effort_flag: str = "") -> ringer.EngineConfig:
    return ringer.EngineConfig(
        name=name,
        bin=name,
        args_template=("{engine_args}", "{spec}"),
        full_access_args=(),
        sandbox_args=(),
        token_regex=None,
        effort_flag=effort_flag,
    )


def command_for(
    worker: ringer.EngineConfig,
    *,
    engine_args: tuple[str, ...] = (),
    reasoning_effort: str = "",
) -> list[str]:
    return ringer.build_worker_command(
        worker,
        taskdir=Path("/tmp/task"),
        spec="do the work",
        full_access=False,
        engine_args=engine_args,
        reasoning_effort=reasoning_effort,
    )


class EngineEffortFlagTests(unittest.TestCase):
    def test_load_engines_reads_effort_flag(self) -> None:
        engines = ringer.load_engines(
            {
                "claude": {
                    "bin": "claude",
                    "args_template": ["-p", "{engine_args}", "{spec}"],
                    "effort_flag": "  --effort  ",
                },
                "opencode": {
                    "bin": "opencode",
                    "args_template": ["run", "{engine_args}", "{spec}"],
                },
            }
        )
        self.assertEqual("--effort", engines["claude"].effort_flag)
        self.assertEqual("", engines["opencode"].effort_flag)

    def test_load_engines_inherits_effort_flag_when_absent(self) -> None:
        base = dataclasses.replace(ringer.built_in_codex_engine(), effort_flag="--effort")
        with mock.patch.object(ringer, "built_in_codex_engine", return_value=base):
            engines = ringer.load_engines({"codex": {}})
        self.assertEqual("--effort", engines["codex"].effort_flag)

    def test_build_worker_command_inserts_configured_effort_flag(self) -> None:
        command = command_for(engine("claude", effort_flag="--effort"), reasoning_effort="high")
        effort_at = command.index("--effort")
        self.assertEqual(["--effort", "high"], command[effort_at:effort_at + 2])
        self.assertNotIn("--variant", command)

    def test_build_worker_command_defaults_to_variant(self) -> None:
        command = command_for(engine("opencode"), reasoning_effort="low")
        variant_at = command.index("--variant")
        self.assertEqual(["--variant", "low"], command[variant_at:variant_at + 2])

    def test_existing_effort_flag_in_engine_args_is_not_repeated(self) -> None:
        separate = command_for(
            engine("claude", effort_flag="--effort"),
            engine_args=("--effort", "max"),
            reasoning_effort="high",
        )
        self.assertEqual(1, separate.count("--effort"))
        self.assertEqual("max", separate[separate.index("--effort") + 1])
        self.assertNotIn("--variant", separate)

        joined = command_for(
            engine("claude", effort_flag="--effort"),
            engine_args=("--effort=max",),
            reasoning_effort="high",
        )
        self.assertEqual(["claude", "--effort=max", "do the work"], joined)
        self.assertNotIn("--variant", joined)

    def test_effort_values_from_command_reads_effort_flag(self) -> None:
        self.assertEqual(
            ["high"],
            ringer._effort_values_from_command(["claude", "--effort", "high"]),
        )
        self.assertEqual(
            ["low"],
            ringer._effort_values_from_command(["claude", "--effort=low"]),
        )
