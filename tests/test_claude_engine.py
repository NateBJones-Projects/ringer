#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path

from ringer import (
    ENGINE_INSTALL_HINTS,
    build_worker_command,
    load_engines,
    parse_reported_model,
)


class ClaudeEngineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = load_engines(None)["claude"]

    def test_requires_explicit_model_and_hard_fails_without_sandbox(self) -> None:
        self.assertEqual("", self.engine.model_default)
        command = build_worker_command(
            self.engine,
            taskdir=Path("/tmp/claude-task"),
            spec="write result.txt",
            full_access=False,
            model="sonnet",
        )
        self.assertEqual("claude", Path(command[0]).name)
        self.assertIn("--print", command)
        self.assertIn("stream-json", command)
        self.assertIn("acceptEdits", command)
        self.assertEqual(
            {"mcpServers": {}},
            json.loads(command[command.index("--mcp-config") + 1]),
        )
        settings = json.loads(command[command.index("--settings") + 1])
        self.assertEqual(
            {
                "enabled": True,
                "failIfUnavailable": True,
                "allowUnsandboxedCommands": False,
            },
            settings["sandbox"],
        )
        self.assertEqual("sonnet", command[command.index("--model") + 1])
        self.assertEqual("write result.txt", command[-1])

    def test_full_access_is_an_explicit_permission_bypass(self) -> None:
        command = build_worker_command(
            self.engine,
            taskdir=Path("/tmp/claude-task"),
            spec="write result.txt",
            full_access=True,
            model="sonnet",
        )
        self.assertIn("--dangerously-skip-permissions", command)
        self.assertNotIn("--settings", command)

    def test_stream_reports_model_and_install_hint_is_actionable(self) -> None:
        line = '{"type":"system","subtype":"init","model":"claude-sonnet-5"}'
        self.assertEqual(
            "claude-sonnet-5",
            parse_reported_model(line, self.engine.model_report_regex),
        )
        self.assertIn("https://claude.ai/install.sh", ENGINE_INSTALL_HINTS["claude"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
