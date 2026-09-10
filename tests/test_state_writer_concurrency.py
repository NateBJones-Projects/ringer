#!/usr/bin/env python3
"""Concurrent flushes must not fight over one temp file.

Regression: StateWriter.flush wrote `<run>.json.tmp` — one fixed name for
every writer. The background writer thread and an explicit flush (a signal
handler, set_port) overlap routinely, so the first os.replace consumed the
temp file and the second raised FileNotFoundError. That crashed the process
during shutdown and made two signal tests fail ~30% of runs.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ringer import EngineConfig, StateWriter, TaskRuntime, TaskSpec


class StateWriterConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.state_dir = Path(self._temp.name)

    def _writer(self) -> StateWriter:
        taskdir = self.state_dir / "task"
        taskdir.mkdir(parents=True, exist_ok=True)
        log_path = taskdir / "worker.log"
        log_path.write_text("worker output\n", encoding="utf-8")
        runtime = TaskRuntime(
            task=TaskSpec(
                key="task",
                spec="Do the thing described here in full.",
                check="true",
                engine="mock",
            ),
            taskdir=taskdir,
            log_path=log_path,
            status="running",
            attempts=1,
            spec_short="do the thing",
        )
        runtime.started_at_monotonic = 1.0
        return StateWriter(
            "run-concurrency",
            "Concurrency Run",
            "test-agent",
            self.state_dir,
            {
                "mock": EngineConfig(
                    name="mock",
                    bin=sys.executable,
                    args_template=("-c", "pass"),
                    full_access_args=(),
                    sandbox_args=(),
                )
            },
            datetime(2026, 7, 28, tzinfo=timezone.utc),
            [runtime],
            threading.RLock(),
        )

    def test_parallel_flushes_never_lose_their_temp_file(self) -> None:
        writer = self._writer()
        errors: list[BaseException] = []
        start = threading.Event()

        def hammer() -> None:
            start.wait()
            for _ in range(25):
                try:
                    writer.flush()
                except BaseException as exc:  # noqa: BLE001 - the point is to catch any
                    errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(6)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()

        self.assertEqual([], errors, f"concurrent flush raised: {errors[:3]}")

    def test_the_state_file_stays_parseable_under_concurrent_flushes(self) -> None:
        writer = self._writer()
        stop = threading.Event()
        bad: list[str] = []

        def flusher() -> None:
            while not stop.is_set():
                writer.flush()

        def reader() -> None:
            for _ in range(60):
                try:
                    text = writer.path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    continue
                if not text:
                    continue
                try:
                    json.loads(text)
                except json.JSONDecodeError as exc:
                    bad.append(f"{exc}: {text[:120]!r}")

        writer.flush()
        writers = [threading.Thread(target=flusher) for _ in range(3)]
        for thread in writers:
            thread.start()
        readers = [threading.Thread(target=reader) for _ in range(2)]
        for thread in readers:
            thread.start()
        for thread in readers:
            thread.join()
        stop.set()
        for thread in writers:
            thread.join()

        self.assertEqual([], bad, f"reader saw a torn state file: {bad[:2]}")

    def test_no_temp_files_are_left_behind(self) -> None:
        writer = self._writer()
        for _ in range(10):
            writer.flush()
        leftovers = [p.name for p in writer.path.parent.glob("*.tmp")]
        self.assertEqual([], leftovers, f"temp files left behind: {leftovers}")


if __name__ == "__main__":
    unittest.main()
