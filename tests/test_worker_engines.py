#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path

from ringer import (
    ENGINE_INSTALL_HINTS,
    build_worker_command,
    load_engines,
    load_model_identity_registry,
    parse_reported_model,
)


class CursorEngineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = load_engines(None)["cursor"]

    def test_cursor_requires_an_explicit_model_and_keeps_sandbox_enabled(self) -> None:
        self.assertEqual("", self.engine.model_default)
        taskdir = Path("/tmp/cursor-task")
        command = build_worker_command(
            self.engine,
            taskdir=taskdir,
            spec="write result.txt",
            full_access=False,
            model="composer-2.5-fast",
        )
        self.assertEqual(
            [
                self.engine.bin,
                "--print",
                "--output-format",
                "stream-json",
                "--trust",
                "--workspace",
                str(taskdir),
                "--force",
                "--sandbox",
                "enabled",
                "--model",
                "composer-2.5-fast",
                "write result.txt",
            ],
            command,
        )

    def test_cursor_full_access_is_explicitly_sandbox_disabled(self) -> None:
        command = build_worker_command(
            self.engine,
            taskdir=Path("/tmp/cursor-task"),
            spec="write result.txt",
            full_access=True,
            model="grok-4.5-fast-high",
        )
        self.assertIn("disabled", command)
        self.assertNotIn("enabled", command)

    def test_cursor_stream_reports_model_and_has_install_hint(self) -> None:
        line = '{"type":"system","subtype":"init","model":"composer-2.5-fast"}'
        self.assertEqual(
            "composer-2.5-fast",
            parse_reported_model(
                line,
                self.engine.model_report_regex,
                dict(self.engine.model_report_aliases),
            ),
        )
        real_composer_init = (
            '{"type":"system","subtype":"init","model":"Composer 2.5 Fast"}'
        )
        self.assertEqual(
            "composer-2.5-fast",
            parse_reported_model(
                real_composer_init,
                self.engine.model_report_regex,
                dict(self.engine.model_report_aliases),
            ),
        )
        real_grok_init = (
            '{"type":"system","subtype":"init",'
            '"model":"Cursor Grok 4.5 Medium Fast"}'
        )
        self.assertEqual(
            "grok-4.5-fast-high",
            parse_reported_model(
                real_grok_init,
                self.engine.model_report_regex,
                dict(self.engine.model_report_aliases),
            ),
        )
        self.assertIn("https://cursor.com/install", ENGINE_INSTALL_HINTS["cursor"])

    def test_cursor_models_have_verified_harness_identities(self) -> None:
        registry_path = Path(__file__).resolve().parents[1] / "registry" / "model-identity.toml"
        registry = load_model_identity_registry(registry_path)

        composer = registry.resolve("cursor", "composer-2.5-fast")
        self.assertEqual(
            ("Composer 2.5 Fast", "Cursor (Anysphere)", "Cursor Agent", "Cursor account"),
            (composer.model_display, composer.lab, composer.harness, composer.access),
        )

        grok = registry.resolve("cursor", "grok-4.5-fast-high")
        self.assertEqual(
            ("Grok 4.5 Medium Fast", "xAI", "Cursor Agent", "Cursor account"),
            (grok.model_display, grok.lab, grok.harness, grok.access),
        )


class ClaudeEngineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = load_engines(None)["claude"]

    def test_claude_requires_an_explicit_model_and_hard_fails_without_sandbox(self) -> None:
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

    def test_claude_full_access_is_an_explicit_permission_bypass(self) -> None:
        command = build_worker_command(
            self.engine,
            taskdir=Path("/tmp/claude-task"),
            spec="write result.txt",
            full_access=True,
            model="sonnet",
        )
        self.assertIn("--dangerously-skip-permissions", command)
        self.assertNotIn("--settings", command)

    def test_claude_stream_reports_model_and_has_install_hint(self) -> None:
        line = '{"type":"system","subtype":"init","model":"claude-sonnet-5"}'
        self.assertEqual(
            "claude-sonnet-5",
            parse_reported_model(line, self.engine.model_report_regex),
        )
        self.assertIn("https://claude.ai/install.sh", ENGINE_INSTALL_HINTS["claude"])

    def test_claude_reported_model_has_verified_subscription_identity(self) -> None:
        registry_path = Path(__file__).resolve().parents[1] / "registry" / "model-identity.toml"
        identity = load_model_identity_registry(registry_path).resolve(
            "claude", "claude-sonnet-5"
        )
        self.assertEqual(
            ("Claude Sonnet 5", "Anthropic", "Claude Code", "Claude Pro subscription"),
            (identity.model_display, identity.lab, identity.harness, identity.access),
        )


class OpenCodeEngineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = load_engines(None)["opencode"]

    def test_opencode_requires_an_explicit_openrouter_model_and_uses_linux_wrapper(self) -> None:
        self.assertEqual("", self.engine.model_default)
        taskdir = Path("/tmp/opencode-task")
        command = build_worker_command(
            self.engine,
            taskdir=taskdir,
            spec="write result.txt",
            full_access=False,
            model="openrouter/z-ai/glm-5.2",
        )
        self.assertEqual("opencode-sandboxed-linux.sh", Path(command[0]).name)
        self.assertEqual(str(taskdir), command[1])
        self.assertNotIn("--no-sandbox", command)
        self.assertIn("--pure", command)
        self.assertIn("--auto", command)
        self.assertEqual("openrouter/z-ai/glm-5.2", command[command.index("--model") + 1])
        self.assertEqual(str(taskdir), command[command.index("--dir") + 1])
        self.assertEqual("write result.txt", command[-1])

    def test_opencode_full_access_is_an_explicit_wrapper_bypass(self) -> None:
        command = build_worker_command(
            self.engine,
            taskdir=Path("/tmp/opencode-task"),
            spec="write result.txt",
            full_access=True,
            model="openrouter/z-ai/glm-5.2",
        )
        self.assertEqual("--no-sandbox", command[2])
        self.assertIn("https://opencode.ai/install", ENGINE_INSTALL_HINTS["opencode"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
