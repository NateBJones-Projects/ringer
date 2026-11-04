#!/usr/bin/env python3
"""Per-engine spawn stagger: config parsing, SpawnGate slot math, end-to-end spacing.

Engines whose instances share local state (OpenCode: one SQLite state DB per
machine) fail their cold start when several workers spawn in the same instant.
spawn_stagger_s spaces spawns of the same engine apart; 0 preserves the old
spawn-all-at-once behavior.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import SpawnGate, load_engines  # noqa: E402


def toml_string(value: object) -> str:
    return json.dumps(str(value))


def engine_section(**overrides: object) -> dict[str, object]:
    section: dict[str, object] = {
        "bin": "worker-bin",
        "args_template": ["{spec}"],
        "sandbox_args": [],
        "full_access_args": [],
    }
    section.update(overrides)
    return section


class SpawnStaggerConfigTests(unittest.TestCase):
    def test_defaults_to_zero(self) -> None:
        engines = load_engines({"opencode": engine_section()})
        self.assertEqual(0.0, engines["opencode"].spawn_stagger_s)

    def test_builtin_codex_defaults_to_zero(self) -> None:
        engines = load_engines(None)
        self.assertEqual(0.0, engines["codex"].spawn_stagger_s)

    def test_parses_float_and_int(self) -> None:
        engines = load_engines(
            {
                "opencode": engine_section(spawn_stagger_s=2.5),
                "other": engine_section(spawn_stagger_s=3),
            }
        )
        self.assertEqual(2.5, engines["opencode"].spawn_stagger_s)
        self.assertEqual(3.0, engines["other"].spawn_stagger_s)

    def test_negative_rejected(self) -> None:
        with self.assertRaises(ValueError):
            load_engines({"opencode": engine_section(spawn_stagger_s=-1)})

    def test_inf_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            load_engines({"opencode": engine_section(spawn_stagger_s=float("inf"))})

    def test_nan_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            load_engines({"opencode": engine_section(spawn_stagger_s=float("nan"))})

    def test_string_rejected(self) -> None:
        with self.assertRaises(ValueError):
            load_engines({"opencode": engine_section(spawn_stagger_s="2")})

    def test_bool_rejected(self) -> None:
        with self.assertRaises(ValueError):
            load_engines({"opencode": engine_section(spawn_stagger_s=True)})


class SpawnGateTests(unittest.TestCase):
    def make_gate(self, clock: dict[str, float], sleeps: list[float]) -> SpawnGate:
        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock["t"] += seconds

        return SpawnGate(now=lambda: clock["t"], sleep=fake_sleep)

    def test_zero_stagger_never_sleeps(self) -> None:
        clock = {"t": 100.0}
        sleeps: list[float] = []
        gate = self.make_gate(clock, sleeps)
        waited = asyncio.run(gate.wait_turn("opencode", 0.0))
        self.assertEqual(0.0, waited)
        self.assertEqual([], sleeps)

    def test_concurrent_claims_space_out(self) -> None:
        clock = {"t": 100.0}
        sleeps: list[float] = []
        gate = self.make_gate(clock, sleeps)
        spawn_times: list[float] = []

        async def claim() -> None:
            await gate.wait_turn("opencode", 2.0)
            spawn_times.append(clock["t"])

        async def claim_three() -> None:
            # Fire all claims in one loop pass, the way asyncio.gather fires
            # every _run_task at once. The contract is the spawn TIMES: each
            # exactly one stagger after the previous, regardless of when the
            # individual waits were computed.
            await asyncio.gather(claim(), claim(), claim())

        asyncio.run(claim_three())
        self.assertEqual([100.0, 102.0, 104.0], sorted(spawn_times))

    def test_engines_do_not_block_each_other(self) -> None:
        clock = {"t": 100.0}
        sleeps: list[float] = []
        gate = self.make_gate(clock, sleeps)

        async def claim_pairs() -> tuple[float, float]:
            await gate.wait_turn("opencode", 2.0)
            return await gate.wait_turn("codex", 2.0), await gate.wait_turn(
                "opencode", 2.0
            )

        codex_wait, opencode_wait = asyncio.run(claim_pairs())
        self.assertEqual(0.0, codex_wait)
        self.assertGreater(opencode_wait, 0.0)

    def test_no_wait_once_gap_has_elapsed(self) -> None:
        clock = {"t": 100.0}
        sleeps: list[float] = []
        gate = self.make_gate(clock, sleeps)

        async def claim_then_wait_out_the_gap() -> float:
            await gate.wait_turn("opencode", 2.0)
            clock["t"] += 10.0
            return await gate.wait_turn("opencode", 2.0)

        self.assertEqual(0.0, asyncio.run(claim_then_wait_out_the_gap()))


class SpawnStaggerEndToEndTests(unittest.TestCase):
    def test_parallel_mock_workers_spawn_spaced_apart(self) -> None:
        stagger_s = 0.6
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            ringer_home = root / "ringer-home"
            state_dir = root / "state"
            workdir = root / "work"
            config_path = root / "config.toml"
            manifest_path = root / "manifest.json"

            home.mkdir()
            ringer_home.mkdir()

            config_path.write_text(
                "\n".join(
                    [
                        f"state_dir = {toml_string(state_dir)}",
                        "",
                        "[eval]",
                        'backend = "jsonl"',
                        f"jsonl_path = {toml_string(root / 'runs.jsonl')}",
                        "",
                        "[artifact]",
                        "enabled = false",
                        "",
                        "[engines.mock]",
                        f"bin = {toml_string(sys.executable)}",
                        "args_template = [",
                        f"  {toml_string(ROOT / 'engines' / 'mock_worker.py')},",
                        '  "{spec}",',
                        "]",
                        "sandbox_args = []",
                        "full_access_args = []",
                        f"spawn_stagger_s = {stagger_s}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            def mock_task(index: int) -> dict[str, object]:
                return {
                    "key": f"stagger-task-{index}",
                    "engine": "mock",
                    "spec": (
                        "You are the deterministic mock worker. Write only the "
                        "file described in this MOCK_FILE block so the executed "
                        "check can verify the spawn-stagger path.\n"
                        f"MOCK_FILE: out-{index}.txt\n"
                        f"stagger {index}\n"
                        "MOCK_END"
                    ),
                    "check": (
                        f"grep -q stagger out-{index}.txt || "
                        f"{{ echo FAIL: out-{index}.txt missing marker; exit 1; }}"
                    ),
                    "expect_files": [f"out-{index}.txt"],
                }

            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "spawn-stagger-test",
                        "workdir": str(workdir),
                        "max_parallel": 3,
                        "worktrees": False,
                        "tasks": [mock_task(index) for index in range(1, 4)],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["RINGER_HOME"] = str(ringer_home)
            env["XDG_CONFIG_HOME"] = str(root / "xdg-config")

            proc = subprocess.run(
                [
                    sys.executable,
                    "ringer.py",
                    "run",
                    str(manifest_path),
                    "--config",
                    str(config_path),
                    "--no-dashboard",
                    "--identity",
                    "stagger-test",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=60,
            )

            combined_output = proc.stdout + proc.stderr
            self.assertEqual(0, proc.returncode, combined_output)

            spawn_times: list[datetime] = []
            stagger_lines = 0
            for index in range(1, 4):
                log_text = (
                    workdir / f"stagger-task-{index}" / "worker.log"
                ).read_text(encoding="utf-8")
                match = re.search(
                    r"^\[ringer\.py\] attempt 1 started (\S+)$",
                    log_text,
                    flags=re.MULTILINE,
                )
                self.assertIsNotNone(match, log_text)
                spawn_times.append(datetime.fromisoformat(match.group(1)))
                stagger_lines += len(
                    re.findall(r"spawn stagger: waited [0-9.]+s", log_text)
                )

            # Three simultaneous claims: the first spawns immediately, the
            # other two wait one and two stagger slots.
            self.assertEqual(2, stagger_lines)
            spawn_times.sort()
            for earlier, later in zip(spawn_times, spawn_times[1:]):
                gap_s = (later - earlier).total_seconds()
                self.assertGreaterEqual(gap_s, stagger_s * 0.75, spawn_times)

            rows = [
                json.loads(line)
                for line in (root / "runs.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(3, len(rows))
            self.assertEqual(1, sum(row["spawn_wait_ms"] == 0 for row in rows))
            self.assertEqual(2, sum(row["spawn_wait_ms"] >= 450 for row in rows))
            # The third task waits about 1200 ms; if that leaked into
            # duration_ms, this bound fails loudly, while a fast mock attempt
            # stays far under it.
            self.assertTrue(all(row["duration_ms"] < 1080 for row in rows), rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
