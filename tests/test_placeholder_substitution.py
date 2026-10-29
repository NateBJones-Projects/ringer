#!/usr/bin/env python3
"""build_worker_command substitutes placeholders in a single pass."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import EngineConfig, build_worker_command  # noqa: E402


def make_engine(template: tuple[str, ...], model_default: str = "sonnet") -> EngineConfig:
    return EngineConfig(
        name="fake",
        bin="/usr/bin/fake",
        args_template=template,
        full_access_args=("--full",),
        sandbox_args=("--sandbox",),
        token_regex=None,
        model_default=model_default,
    )


def build(template, spec, taskdir="/tmp/task", model="", model_default="sonnet", **kw):
    return build_worker_command(
        make_engine(template, model_default),
        taskdir=Path(taskdir),
        spec=spec,
        full_access=False,
        model=model,
        **kw,
    )


class PlaceholderSubstitutionTests(unittest.TestCase):
    def test_spec_with_placeholder_text_is_not_rewritten(self) -> None:
        spec = 'write "--model", "{model}" into {taskdir}/cfg'
        cmd = build(("-m", "{model}", "--dir", "{taskdir}", "{spec}"), spec, model="opus")
        self.assertEqual(cmd, ["/usr/bin/fake", "-m", "opus", "--dir", "/tmp/task", spec])

    def test_model_and_taskdir_values_are_inserted_unchanged(self) -> None:
        cmd = build(("{model}", "{spec}"), "S", model="x/{taskdir}/{spec}")
        self.assertEqual(cmd[1:], ["x/{taskdir}/{spec}", "S"])
        cmd = build(("{taskdir}", "{model}"), "S", taskdir="/tmp/{model}/d", model="opus")
        self.assertEqual(cmd[1:], ["/tmp/{model}/d", "opus"])

    def test_regex_metacharacters_in_spec_pass_through(self) -> None:
        spec = r"a\1 b\g<0> $& C:\Users\me\new \\ ${x}"
        cmd = build(("{spec}", "pre-{spec}-post"), spec)
        self.assertEqual(cmd[1:], [spec, "pre-" + spec + "-post"])

    def test_multiple_placeholders_in_one_item(self) -> None:
        cmd = build(("--out={taskdir}/{model}.log",), "S", model="opus")
        self.assertEqual(cmd[1], "--out=/tmp/task/opus.log")

    def test_unknown_placeholder_and_empty_model_and_engine_args(self) -> None:
        cmd = build(
            ("{unknown}", "[{model}]", "{engine_args}"),
            "S",
            model_default="",
            engine_args=("{model}", "{spec}"),
        )
        self.assertEqual(cmd[1:], ["{unknown}", "[]", "{model}", "{spec}"])


if __name__ == "__main__":
    unittest.main()
