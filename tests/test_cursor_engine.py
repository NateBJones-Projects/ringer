#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

from ringer import (
    ENGINE_INSTALL_HINTS,
    build_worker_command,
    load_engines,
    parse_reported_model,
)


class CursorEngineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = load_engines(None)["cursor"]

    def test_requires_explicit_model_and_keeps_sandbox_enabled(self) -> None:
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

    def test_full_access_explicitly_disables_cursor_sandbox(self) -> None:
        command = build_worker_command(
            self.engine,
            taskdir=Path("/tmp/cursor-task"),
            spec="write result.txt",
            full_access=True,
            model="grok-4.5-fast-high",
        )
        self.assertIn("disabled", command)
        self.assertNotIn("enabled", command)

    def test_stream_model_labels_are_canonicalized_to_request_slugs(self) -> None:
        aliases = dict(self.engine.model_report_aliases)
        fixtures = {
            "composer-2.5-fast": "composer-2.5-fast",
            "Composer 2.5 Fast": "composer-2.5-fast",
            "Cursor Grok 4.5 Medium Fast": "grok-4.5-fast-high",
        }
        for reported, expected in fixtures.items():
            with self.subTest(reported=reported):
                line = f'{{"type":"system","subtype":"init","model":"{reported}"}}'
                self.assertEqual(
                    expected,
                    parse_reported_model(
                        line,
                        self.engine.model_report_regex,
                        aliases,
                    ),
                )
        self.assertIn("https://cursor.com/install", ENGINE_INSTALL_HINTS["cursor"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
