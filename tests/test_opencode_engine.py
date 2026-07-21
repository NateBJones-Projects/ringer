#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from ringer import build_worker_command, built_in_opencode_engine, load_engines


class OpenCodeEngineContractTests(unittest.TestCase):
    def test_linux_and_macos_select_their_native_wrappers(self) -> None:
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(
                "opencode-sandboxed-linux.sh",
                Path(built_in_opencode_engine().bin).name,
            )
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(
                "opencode-sandboxed.sh",
                Path(built_in_opencode_engine().bin).name,
            )

    @unittest.skipUnless(
        sys.platform == "darwin" or sys.platform.startswith("linux"),
        "built-in OpenCode wrappers are POSIX-only",
    )
    def test_opencode_is_built_in_and_requires_an_explicit_model(self) -> None:
        engine = load_engines(None)["opencode"]
        self.assertEqual("", engine.model_default)
        taskdir = Path("/tmp/opencode-task")
        command = build_worker_command(
            engine,
            taskdir=taskdir,
            spec="write result.txt",
            full_access=False,
            model="openrouter/z-ai/glm-5.2",
        )
        self.assertEqual(str(taskdir), command[1])
        self.assertNotIn("--no-sandbox", command)
        self.assertIn("--pure", command)
        self.assertIn("--auto", command)
        self.assertEqual(
            "openrouter/z-ai/glm-5.2",
            command[command.index("--model") + 1],
        )
        self.assertEqual(str(taskdir), command[command.index("--dir") + 1])
        self.assertEqual("write result.txt", command[-1])

    @unittest.skipUnless(
        sys.platform == "darwin" or sys.platform.startswith("linux"),
        "built-in OpenCode wrappers are POSIX-only",
    )
    def test_full_access_uses_the_existing_two_part_gate_escape(self) -> None:
        command = build_worker_command(
            load_engines(None)["opencode"],
            taskdir=Path("/tmp/opencode-task"),
            spec="write result.txt",
            full_access=True,
            model="openrouter/z-ai/glm-5.2",
        )
        self.assertEqual("--no-sandbox", command[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
