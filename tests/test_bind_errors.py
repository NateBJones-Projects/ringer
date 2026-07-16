#!/usr/bin/env python3
from __future__ import annotations

import errno
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402


class BindErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name)

    def persistent_server(self) -> ringer.PersistentHudServer:
        return ringer.PersistentHudServer(self.state_dir, preferred_port=8765, open_viewer=False)

    def dashboard(self) -> ringer.Dashboard:
        return ringer.Dashboard(self.state_dir / "state.json", preferred_port=8765, open_viewer=False)

    def test_persistent_hud_eperm_reports_environment_limit(self) -> None:
        error = PermissionError(errno.EPERM, "Operation not permitted")
        with mock.patch.object(ringer, "ThreadingHTTPServer", side_effect=error):
            with self.assertRaises(ringer.BindNotPermittedError) as caught:
                self.persistent_server().start()

        self.assertNotIn("already in use", str(caught.exception))

    def test_persistent_hud_address_in_use_keeps_existing_advice(self) -> None:
        error = OSError(errno.EADDRINUSE, "Address already in use")
        with mock.patch.object(ringer, "ThreadingHTTPServer", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                self.persistent_server().start()

        self.assertNotIsInstance(caught.exception, ringer.BindNotPermittedError)
        self.assertIn(
            "that port is already in use. Use --port to choose another port.",
            str(caught.exception),
        )

    def test_persistent_hud_eacces_reports_underlying_error(self) -> None:
        error = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(ringer, "ThreadingHTTPServer", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                self.persistent_server().start()

        self.assertNotIsInstance(caught.exception, ringer.BindNotPermittedError)
        self.assertIn(str(error), str(caught.exception))
        self.assertNotIn("already in use", str(caught.exception))

    def test_dashboard_eperm_stops_after_first_bind_attempt(self) -> None:
        error = PermissionError(errno.EPERM, "Operation not permitted")
        with mock.patch.object(ringer, "ThreadingHTTPServer", side_effect=error) as constructor:
            with self.assertRaises(ringer.BindNotPermittedError):
                self.dashboard().start()

        self.assertEqual(1, constructor.call_count)

    def test_bind_not_permitted_error_is_runtime_error(self) -> None:
        self.assertTrue(issubclass(ringer.BindNotPermittedError, RuntimeError))


if __name__ == "__main__":
    unittest.main(verbosity=2)
