from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKS = ROOT / "templates" / "competitive-teardown" / "checks"


def load_check(name: str):
    spec = importlib.util.spec_from_file_location(f"teardown_{name}", CHECKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TeardownUrlRegexTests(unittest.TestCase):
    """Both teardown checks must find ordinary cited URLs.

    The URL_RE character class once closed early on an unescaped `]`, so it
    matched no real URL and every lane failed with "cites 0 URLs".
    """

    TEXT = (
        "Pricing per https://example.com/pricing and the launch post "
        "[announcement](https://example.org/blog/launch). "
        "Archived at <https://example.net/a?b=1>."
    )
    EXPECTED = {
        "https://example.com/pricing",
        "https://example.org/blog/launch",
        "https://example.net/a?b=1",
    }

    def test_teardown_check_extracts_urls(self) -> None:
        found = set(load_check("teardown_check").extract_urls(self.TEXT))
        self.assertTrue(self.EXPECTED <= found, found)

    def test_synthesis_check_extracts_urls(self) -> None:
        found = load_check("synthesis_check").extract_urls(self.TEXT)
        self.assertEqual(self.EXPECTED, found)

    def test_url_stops_at_delimiters(self) -> None:
        for name in ("teardown_check", "synthesis_check"):
            pattern = load_check(name).URL_RE
            for text in ("(https://x.com/a)", "[https://x.com/a]", '"https://x.com/a"', "https://x.com/a\\n"):
                with self.subTest(check=name, text=text):
                    self.assertEqual("https://x.com/a", pattern.search(text).group(0))


if __name__ == "__main__":
    unittest.main()
