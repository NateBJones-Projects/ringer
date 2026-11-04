#!/usr/bin/env python3
"""Ringside tab reuse regression tests; no real browser or workers are launched.

Run from the worktree root:
    python3 -m unittest -v tests.test_hud_reuse_tab tests.test_hud_single_tab tests.test_hud_server

Server fixtures use a free localhost port and temporary state, then stop the
server and remove the state. Browser opens and HUD process launches are mocked.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from tests.test_hud_single_tab import config as hud_config  # noqa: E402


class HudReuseTabTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = hud_config(self.root)
        self.output = io.StringIO()
        for name, value in (("hud_is_alive", True), ("hud_is_healthy", True)):
            if not hasattr(ringer, name):  # hud_is_healthy exists only on newer checkouts
                continue
            patcher = mock.patch.object(ringer, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        lookup = mock.patch.object(ringer, "hud_viewer_seconds_since_ping", return_value=3.0)
        self.lookup = lookup.start()
        self.addCleanup(lookup.stop)
        opener = mock.patch.object(ringer, "open_in_browser")
        self.opener = opener.start()
        self.addCleanup(opener.stop)
        popen = mock.patch.object(ringer.subprocess, "Popen")
        self.popen = popen.start()
        self.addCleanup(popen.stop)

    def run_hud(self, *, force_open: bool = False, open_viewer: bool = True) -> None:
        with contextlib.redirect_stdout(self.output):
            result = ringer.run_persistent_hud(
                self.config, port=None, open_viewer=open_viewer, force_open=force_open,
            )
        self.assertEqual(0, result)

    def assert_opened_once(self) -> None:
        self.opener.assert_called_once_with("http://127.0.0.1:8700")

    def test_alive_recent_viewer_reuses_tab_and_prints_one_line(self) -> None:
        self.run_hud()
        self.opener.assert_not_called()
        self.lookup.assert_called_once_with(8700)
        self.assertEqual(
            "Ringside is already open in a browser tab: http://127.0.0.1:8700 "
            "(not opening another; use --force-open to open one anyway)\n",
            self.output.getvalue(),
        )

    def test_alive_no_viewer_opens_once(self) -> None:
        self.lookup.return_value = None
        self.run_hud()
        self.assert_opened_once()

    def test_alive_stale_viewer_opens_once(self) -> None:
        self.lookup.return_value = 60.0
        self.run_hud()
        self.assert_opened_once()

    def test_viewer_at_fifteen_seconds_opens_once(self) -> None:
        self.lookup.return_value = 15.0
        self.run_hud()
        self.assert_opened_once()

    def test_force_open_opens_even_with_recent_viewer(self) -> None:
        args = ringer.build_parser().parse_args(["hud", "--force-open"])
        self.run_hud(force_open=args.force_open)
        self.assert_opened_once()
        self.lookup.assert_not_called()

    def test_reuse_disabled_opens_even_with_recent_viewer(self) -> None:
        self.config = replace(self.config, reuse_open_tab=False)
        self.run_hud()
        self.assert_opened_once()
        self.lookup.assert_not_called()

    def test_viewer_lookup_failure_opens_once(self) -> None:
        self.lookup.side_effect = OSError("viewer-status unavailable")
        self.run_hud()
        self.assert_opened_once()

    def test_no_open_does_not_lookup_or_open_viewer(self) -> None:
        self.run_hud(open_viewer=False, force_open=True)
        self.opener.assert_not_called()
        self.lookup.assert_not_called()

    def test_ensure_alive_does_not_lookup_or_open_viewer(self) -> None:
        with contextlib.redirect_stdout(self.output):
            ringer.ensure_hud_running(self.config, open_browser=True)
        self.popen.assert_not_called()
        self.opener.assert_not_called()
        self.lookup.assert_not_called()

    def test_ensure_fresh_hud_reuses_recent_viewer(self) -> None:
        with mock.patch.object(ringer, "hud_is_alive", side_effect=[False, True, True]):
            with contextlib.redirect_stdout(self.output):
                ringer.ensure_hud_running(self.config, open_browser=True)
        self.popen.assert_called_once()
        self.lookup.assert_called_once_with(8700)
        self.opener.assert_not_called()

    def test_ensure_fresh_hud_lookup_failure_opens_once(self) -> None:
        self.lookup.side_effect = OSError("older HUD")
        with mock.patch.object(ringer, "hud_is_alive", side_effect=[False, True, True]):
            with contextlib.redirect_stdout(self.output):
                ringer.ensure_hud_running(self.config, open_browser=True)
        self.popen.assert_called_once()
        self.assert_opened_once()

    def test_config_load_defaults_true_and_accepts_false(self) -> None:
        self.assertTrue(self.config.reuse_open_tab)
        path = self.root / "config.toml"
        for hud_table, expected in (
            ("", True),
            ("[hud]\nport = 8701\n", True),
            ("[hud]\nport = 8701\nreuse_open_tab = false\n", False),
        ):
            with self.subTest(hud_table=hud_table):
                path.write_text(f'state_dir = "{self.root / "state"}"\n{hud_table}', encoding="utf-8")
                loaded = ringer.AppConfig.load(path)
                self.assertEqual(expected, loaded.reuse_open_tab)
                self.assertEqual(8701 if hud_table else 8700, loaded.hud_port)


class HudViewerLookupTests(unittest.TestCase):
    def test_lookup_uses_viewer_endpoint_and_short_timeout(self) -> None:
        for value, expected in ((3.0, 3.0), (None, None), (True, None), ("3", None)):
            with self.subTest(value=value):
                response = io.BytesIO(json.dumps({"seconds_since_viewer": value}).encode())
                with mock.patch.object(ringer.urllib.request, "urlopen", return_value=response) as lookup:
                    self.assertEqual(expected, ringer.hud_viewer_seconds_since_ping(8701))
                lookup.assert_called_once_with("http://127.0.0.1:8701/api/viewer-status", timeout=1)

    def test_missing_endpoint_and_invalid_json_are_not_recent_viewers(self) -> None:
        error = HTTPError("http://127.0.0.1:8700/api/viewer-status", 404, "Not Found", None, None)
        with error, mock.patch.object(ringer.urllib.request, "urlopen", side_effect=error):
            self.assertFalse(ringer.hud_has_recent_viewer(8700))
        with mock.patch.object(ringer.urllib.request, "urlopen", return_value=io.BytesIO(b"old HUD")):
            self.assertFalse(ringer.hud_has_recent_viewer(8700))


class HudViewerServerTests(unittest.TestCase):
    def test_viewer_status_before_and_after_ping(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            server = ringer.PersistentHudServer(Path(temp), preferred_port=0, open_viewer=False)
            with contextlib.redirect_stdout(io.StringIO()):
                port = server.start()
            try:
                base = f"http://127.0.0.1:{port}"
                with urlopen(f"{base}/api/viewer-status", timeout=5) as response:
                    self.assertEqual(200, response.status)
                    self.assertEqual("no-store", response.headers["Cache-Control"])
                    self.assertEqual({"seconds_since_viewer": None}, json.load(response))
                self.assertIsNone(ringer.hud_viewer_seconds_since_ping(port))
                with urlopen(f"{base}/api/viewer-ping", timeout=5) as response:
                    self.assertEqual(204, response.status)
                    self.assertEqual("no-store", response.headers["Cache-Control"])
                    self.assertEqual(b"", response.read())
                with urlopen(f"{base}/api/viewer-status", timeout=5) as response:
                    seconds = json.load(response)["seconds_since_viewer"]
                self.assertIsInstance(seconds, float)
                self.assertGreaterEqual(seconds, 0)
                self.assertLess(seconds, 5)
                self.assertTrue(ringer.hud_has_recent_viewer(port))
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
