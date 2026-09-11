#!/usr/bin/env python3
"""Structural checks for registry/model-capabilities/*.toml.

Every model sheet must parse as TOML, carry the required sections, declare a
unique model key, and back its claims with source records that carry a claim,
an http(s) url, an ISO accessed date, and a quote. Platform sheets (no [model]
table) must be explicitly listed in PLATFORM_SHEETS and only need well-formed
sources — any other sheet missing its [model] table fails instead of being
silently exempted.
"""
from __future__ import annotations

import tomllib
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SHEETS_DIR = ROOT / "registry" / "model-capabilities"

# Sheets that intentionally have no [model] table (platform-level facts).
PLATFORM_SHEETS = {"openrouter-platform.toml"}

REQUIRED_MODEL_SECTIONS = {
    "model",
    "api",
    "caching",
    "tool_calling",
    "reasoning",
    "limits",
    "pricing",
    "sources",
}

# (caching key, pricing key) pairs that duplicate the same fact; when both are
# numeric they must agree.
MIRRORED_PRICE_FIELDS = (
    ("read_pricing_per_m", "cache_read_per_m"),
    ("write_pricing_per_m", "cache_write_per_m"),
)

_SHEETS: dict[Path, dict] | None = None


def load_sheets() -> dict[Path, dict]:
    """Parse every sheet once. Unparseable files are skipped here so the other
    tests still cover the rest of the corpus; test_every_sheet_parses is the
    single test that attributes the parse error to its sheet."""
    global _SHEETS
    if _SHEETS is None:
        _SHEETS = {}
        for path in sorted(SHEETS_DIR.glob("*.toml")):
            try:
                with path.open("rb") as fh:
                    _SHEETS[path] = tomllib.load(fh)
            except tomllib.TOMLDecodeError:
                continue
    return _SHEETS


def model_sheets() -> dict[Path, dict]:
    return {
        path: data
        for path, data in load_sheets().items()
        if path.name not in PLATFORM_SHEETS
    }


class TestCapabilitySheets(unittest.TestCase):
    def test_sheets_dir_has_sheets(self) -> None:
        self.assertTrue(
            sorted(SHEETS_DIR.glob("*.toml")), f"no sheets found in {SHEETS_DIR}"
        )

    def test_every_sheet_parses(self) -> None:
        for path in sorted(SHEETS_DIR.glob("*.toml")):
            with self.subTest(sheet=path.name):
                with path.open("rb") as fh:
                    tomllib.load(fh)

    def test_model_sheets_have_required_sections(self) -> None:
        for path, data in model_sheets().items():
            with self.subTest(sheet=path.name):
                missing = REQUIRED_MODEL_SECTIONS - set(data)
                self.assertFalse(
                    missing,
                    f"{path.name} is missing required sections: {sorted(missing)}"
                    " (platform sheets must be listed in PLATFORM_SHEETS)",
                )

    def test_model_keys_present_and_unique(self) -> None:
        seen: dict[str, str] = {}
        for path, data in model_sheets().items():
            with self.subTest(sheet=path.name):
                key = data.get("model", {}).get("key")
                self.assertTrue(
                    isinstance(key, str) and key.strip(),
                    f"{path.name} has no non-empty model.key",
                )
                self.assertNotIn(
                    key,
                    seen,
                    f"model.key {key!r} appears in both {seen.get(key)} and {path.name}",
                )
                seen[key] = path.name

    def test_mirrored_cache_prices_agree(self) -> None:
        for path, data in model_sheets().items():
            caching = data.get("caching", {})
            pricing = data.get("pricing", {})
            for caching_key, pricing_key in MIRRORED_PRICE_FIELDS:
                a, b = caching.get(caching_key), pricing.get(pricing_key)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    with self.subTest(sheet=path.name, field=pricing_key):
                        self.assertEqual(
                            a,
                            b,
                            f"{path.name}: caching.{caching_key}={a} disagrees "
                            f"with pricing.{pricing_key}={b}",
                        )

    def test_sources_are_complete_records(self) -> None:
        for path, data in load_sheets().items():
            sources = data.get("sources", [])
            with self.subTest(sheet=path.name):
                self.assertTrue(sources, f"{path.name} has no [[sources]] records")
            for i, src in enumerate(sources):
                with self.subTest(sheet=path.name, source=i):
                    for field in ("claim", "quote"):
                        value = src.get(field)
                        self.assertTrue(
                            isinstance(value, str) and value.strip(),
                            f"{path.name} source #{i} has no non-empty {field}",
                        )
                    url = src.get("url", "")
                    parsed = urlparse(url if isinstance(url, str) else "")
                    self.assertTrue(
                        parsed.scheme in ("http", "https") and parsed.netloc,
                        f"{path.name} source #{i} url {url!r} is not http(s)",
                    )
                    accessed = str(src.get("accessed", ""))
                    try:
                        date.fromisoformat(accessed)
                    except ValueError:
                        self.fail(
                            f"{path.name} source #{i} accessed date {accessed!r} "
                            "is not a valid ISO date"
                        )


if __name__ == "__main__":
    unittest.main()
