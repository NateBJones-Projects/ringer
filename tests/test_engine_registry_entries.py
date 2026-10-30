#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

from ringer import load_model_identity_registry


ROOT = Path(__file__).resolve().parents[1]


class EngineRegistryEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_model_identity_registry(
            ROOT / "registry" / "model-identity.toml"
        )

    def test_cursor_composer_has_sourced_canonical_identity(self) -> None:
        identity = self.registry.resolve("cursor", "composer-2.5-fast")
        self.assertEqual(
            (
                "Composer 2.5 Fast",
                "Cursor (Anysphere)",
                "Cursor Agent",
                "Cursor subscription",
            ),
            (
                identity.model_display,
                identity.lab,
                identity.harness,
                identity.access,
            ),
        )
        self.assertEqual("verified", identity.confidence)
        self.assertEqual("https://cursor.com/en-US/composer", identity.source)
        self.assertNotIn(
            ("cursor", "composer-2.5-fast"),
            self.registry.noncanonical_routes,
        )

    def test_cursor_grok_uses_the_jointly_trained_model_source(self) -> None:
        identity = self.registry.resolve("cursor", "grok-4.5-fast-high")
        self.assertEqual("Grok 4.5", identity.model_display)
        self.assertEqual("Cursor + SpaceXAI", identity.lab)
        self.assertEqual("verified", identity.confidence)
        self.assertEqual("https://cursor.com/blog/grok-4-5", identity.source)
        self.assertNotIn(
            ("cursor", "grok-4.5-fast-high"),
            self.registry.noncanonical_routes,
        )

    def test_claude_full_slug_is_verified_but_rolling_alias_is_not(self) -> None:
        exact = self.registry.resolve("claude", "claude-sonnet-5")
        self.assertEqual(
            ("Claude Sonnet 5", "Anthropic", "Claude Code", "Claude Pro subscription"),
            (exact.model_display, exact.lab, exact.harness, exact.access),
        )
        self.assertEqual("verified", exact.confidence)
        self.assertEqual(
            "https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5",
            exact.source,
        )

        alias = self.registry.resolve("claude", "sonnet")
        self.assertTrue(alias.alias)
        self.assertIn("rolling alias", alias.model_display)
        self.assertEqual("unverified", alias.confidence)
        self.assertEqual("https://code.claude.com/docs/en/cli-usage", alias.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
