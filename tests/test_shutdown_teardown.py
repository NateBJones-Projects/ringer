from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RINGER_PATH = ROOT / "ringer.py"
SPEC = importlib.util.spec_from_file_location("ringer_shutdown_module", RINGER_PATH)
assert SPEC is not None and SPEC.loader is not None
ringer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ringer
SPEC.loader.exec_module(ringer)


class _RecordingTransport:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FinishedProcess:
    def __init__(self) -> None:
        self.pid = 12345
        self.returncode = 0
        self._transport = _RecordingTransport()
        self.waited = False

    async def wait(self) -> int:
        self.waited = True
        return 0


class ShutdownTransportTests(unittest.TestCase):
    def test_kill_all_workers_waits_and_closes_owned_transports(self) -> None:
        # A transport left for garbage collection after asyncio.run() closes the
        # loop prints "Event loop is closed" from BaseSubprocessTransport.__del__.
        runner = object.__new__(ringer.RingerRunner)
        proc = _FinishedProcess()
        runner.active_processes = {proc.pid: proc}

        asyncio.run(runner.kill_all_workers())

        self.assertTrue(proc.waited)
        self.assertTrue(proc._transport.closed)
        self.assertEqual(runner.active_processes, {})


if __name__ == "__main__":
    unittest.main()
