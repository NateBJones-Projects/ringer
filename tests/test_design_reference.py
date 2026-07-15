#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import ARTIFACT_BASE_CSS, ArtifactRenderer, render_final_report_html, render_status_html  # noqa: E402

REFERENCE = Path(
    "/private/tmp/claude-501/-Users-jonathanedwards-WORKSPACE-20-CLIENTS-NATE-10-ACTIVE-SYSTEMS-ringer-system/"
    "d4e72b45-c6bd-4928-aaf0-4e3552eb8f04/scratchpad/design-bake/design-reference.html"
)


def css_block(css: str, selector: str) -> str:
    pattern = re.escape(selector) + r"\s*\{(?P<body>.*?)\}"
    match = re.search(pattern, css, re.S)
    if not match:
        raise AssertionError(f"missing CSS block: {selector}")
    return match.group("body")


def media_light_root(css: str) -> str:
    match = re.search(
        r"@media\s*\(prefers-color-scheme:\s*light\)\s*\{\s*:root\s*\{(?P<body>.*?)\}\s*\}",
        css,
        re.S,
    )
    if not match:
        raise AssertionError("missing light media :root block")
    return match.group("body")


def token_values(block: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in re.findall(r"(--[a-z-]+)\s*:\s*([^;]+);", block):
        values[name] = re.sub(r"\s+", "", value)
    return values


class DesignReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.renderer = ArtifactRenderer(Path(self.tmp.name) / "artifacts" / "run.html")

    def test_renderer_tokens_match_design_reference(self) -> None:
        if not REFERENCE.exists():
            self.skipTest(f"design reference is unavailable; regenerate it and point REFERENCE at it: {REFERENCE}")
        reference_css = REFERENCE.read_text(encoding="utf-8")

        expected_dark = token_values(css_block(reference_css, ":root"))
        expected_light = token_values(media_light_root(reference_css))
        expected_dark_override = token_values(css_block(reference_css, ':root[data-theme="dark"]'))
        expected_light_override = token_values(css_block(reference_css, ':root[data-theme="light"]'))

        self.assertEqual(expected_dark, token_values(css_block(ARTIFACT_BASE_CSS, ":root")))
        self.assertEqual(expected_light, token_values(media_light_root(ARTIFACT_BASE_CSS)))
        self.assertEqual(expected_dark_override, token_values(css_block(ARTIFACT_BASE_CSS, ':root[data-theme="dark"]')))
        self.assertEqual(expected_light_override, token_values(css_block(ARTIFACT_BASE_CSS, ':root[data-theme="light"]')))

    def test_live_page_uses_reference_structure(self) -> None:
        render_status_html(
            self.state([self.task("contract-a", "running", attempts=1)]),
            renderer=self.renderer,
        )
        html = render_status_html(
            self.state(
                [
                    self.task("contract-a", "retrying", attempts=2, check_output_tail="FAIL: quoted text not found"),
                    self.task("contract-b", "running", activity="Reading section 4"),
                    self.task("contract-c", "pass"),
                    self.task("contract-d", "queued"),
                ]
            ),
            renderer=self.renderer,
        )

        self.assertIn('<header class="corner">', html)
        self.assertIn('class="live-dot is-live"', html)
        self.assertIn('<div class="rounds"', html)
        self.assertIn('<section class="work"', html)
        self.assertIn('<div class="work-group">', html)
        self.assertIn('<div class="worker">', html)
        self.assertIn('<span class="retry" aria-label="contract-a: sent back — redoing"></span>', html)
        self.assertIn('<span class="working" aria-label="contract-b: working"></span>', html)

    def test_final_page_uses_static_dot(self) -> None:
        html = render_final_report_html(
            self.state([self.task("contract-a", "pass")], finished=True),
            renderer=self.renderer,
        )

        self.assertIn('<span class="live-dot pass" aria-hidden="true"></span>', html)
        self.assertNotIn('class="live-dot is-live"', html)
        self.assertNotIn('http-equiv="refresh"', html)

    def task(
        self,
        key: str,
        status: str,
        *,
        attempts: int = 1,
        elapsed_s: float = 12,
        activity: str | None = None,
        check_output_tail: str = "",
    ) -> dict[str, object]:
        task: dict[str, object] = {
            "key": key,
            "status": status,
            "attempts": attempts,
            "elapsed_s": elapsed_s,
            "check_output_tail": check_output_tail,
        }
        if activity is not None:
            task["activity"] = activity
        return task

    def state(self, tasks: list[dict[str, object]], *, finished: bool = False) -> dict[str, object]:
        return {
            "run_id": "run-123",
            "run_name": "Design Run",
            "identity": "test-agent",
            "state": "finished" if finished else "live",
            "started_at": "2026-07-05T00:00:00+00:00",
            "elapsed_s": 92,
            "finished": finished,
            "report_ready": False,
            "report_path": None,
            "tasks": tasks,
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)
