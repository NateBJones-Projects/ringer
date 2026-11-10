#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ringer  # noqa: E402


class PidIsAliveTests(unittest.TestCase):
    def test_nonpositive_pid_is_not_alive(self) -> None:
        self.assertFalse(ringer.pid_is_alive(0))
        self.assertFalse(ringer.pid_is_alive(-1))

    def test_current_pid_is_alive(self) -> None:
        self.assertTrue(ringer.pid_is_alive(os.getpid()))

    def test_windows_uses_windows_helper(self) -> None:
        with (
            mock.patch.object(ringer.os, "name", "nt"),
            mock.patch.object(
                ringer,
                "_windows_pid_is_alive",
                return_value=False,
                create=True,
            ) as windows_pid_is_alive,
            mock.patch.object(ringer.os, "kill") as kill,
        ):
            result = ringer.pid_is_alive(4242)

        self.assertFalse(result)
        windows_pid_is_alive.assert_called_once_with(4242)
        kill.assert_not_called()

    def test_posix_uses_kill(self) -> None:
        with (
            mock.patch.object(ringer.os, "name", "posix"),
            mock.patch.object(
                ringer, "_windows_pid_is_alive", create=True
            ) as windows_pid_is_alive,
            mock.patch.object(ringer.os, "kill") as kill,
        ):
            result = ringer.pid_is_alive(4242)

        self.assertTrue(result)
        kill.assert_called_once_with(4242, 0)
        windows_pid_is_alive.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "requires Windows")
    def test_real_windows_helper(self) -> None:
        self.assertTrue(ringer._windows_pid_is_alive(os.getpid()))
        self.assertFalse(ringer._windows_pid_is_alive(99999999))


if __name__ == "__main__":
    unittest.main(verbosity=2)
