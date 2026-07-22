#!/usr/bin/env python3
"""Codex probe tasks default to sandbox network access.

Codex's default '--sandbox workspace-write' blocks process-level network while
its built-in web search still works, so live-probe lanes silently fail. A probe
task on the codex engine (not full_access) gets
'sandbox_workspace_write.network_access=true' injected — unless the task set
that key itself (MODEL-NOTES 2026-07-20).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    TaskSpec,
    build_worker_command,
    built_in_codex_engine,
    effective_engine_args,
)

NETWORK_KEY = "sandbox_workspace_write.network_access"


class EffectiveEngineArgsTests(unittest.TestCase):
    def worker_command(self, task: TaskSpec) -> list[str]:
        engine = built_in_codex_engine()
        return build_worker_command(
            engine,
            taskdir=Path("/tmp/taskdir"),
            spec=task.spec,
            full_access=task.full_access,
            engine_args=effective_engine_args(task),
            model=task.model,
        )

    def task(self, **overrides: object) -> TaskSpec:
        base: dict[str, object] = {
            "key": "probe-1",
            "spec": "probe the live gateway and report",
            "check": "test -s report.md",
            "engine": "codex",
            "task_type": "probe",
        }
        base.update(overrides)
        return TaskSpec.from_obj(base)

    def test_a_codex_probe_gets_network_flag(self) -> None:
        cmd = self.worker_command(self.task())
        self.assertIn("-c", cmd)
        self.assertIn(f"{NETWORK_KEY}=true", cmd)
        # The flag rides as a '-c KEY=true' pair.
        idx = cmd.index(f"{NETWORK_KEY}=true")
        self.assertEqual(cmd[idx - 1], "-c")

    def test_b_non_probe_task_untouched(self) -> None:
        task = self.task(task_type="research")
        self.assertEqual(effective_engine_args(task), ())
        cmd = self.worker_command(task)
        self.assertNotIn(f"{NETWORK_KEY}=true", cmd)

    def test_c_explicit_network_setting_wins(self) -> None:
        task = self.task(engine_args=["-c", f"{NETWORK_KEY}=false"])
        # Explicit per-task setting is left untouched — no true injected.
        self.assertEqual(
            effective_engine_args(task), ("-c", f"{NETWORK_KEY}=false")
        )
        cmd = self.worker_command(task)
        self.assertIn(f"{NETWORK_KEY}=false", cmd)
        self.assertNotIn(f"{NETWORK_KEY}=true", cmd)

    def test_d_non_codex_engine_untouched(self) -> None:
        task = self.task(engine="opencode")
        self.assertEqual(effective_engine_args(task), ())

    def test_e_near_miss_key_does_not_suppress(self) -> None:
        # A longer dotted key that merely shares the prefix must NOT be read as
        # an explicit override of the network-access key.
        task = self.task(engine_args=["-c", f"{NETWORK_KEY}_log=true"])
        args = effective_engine_args(task)
        self.assertIn(f"{NETWORK_KEY}=true", args)
        # The pre-existing near-miss pair is preserved alongside the injection.
        self.assertIn(f"{NETWORK_KEY}_log=true", args)

    def test_f_exact_true_assignment_suppresses(self) -> None:
        task = self.task(engine_args=["-c", f"{NETWORK_KEY}=true"])
        self.assertEqual(
            effective_engine_args(task), ("-c", f"{NETWORK_KEY}=true")
        )

    def test_g_exact_false_assignment_suppresses(self) -> None:
        task = self.task(engine_args=["-c", f"{NETWORK_KEY}=false"])
        args = effective_engine_args(task)
        self.assertEqual(args, ("-c", f"{NETWORK_KEY}=false"))
        self.assertNotIn(f"{NETWORK_KEY}=true", args)

    def test_h_whitespace_around_key_still_exact(self) -> None:
        # The KEY compare tolerates surrounding whitespace on the assignment.
        task = self.task(engine_args=["-c", f" {NETWORK_KEY} =false"])
        self.assertNotIn(f"{NETWORK_KEY}=true", effective_engine_args(task))

    def test_full_access_probe_untouched(self) -> None:
        task = self.task(full_access=True)
        self.assertEqual(effective_engine_args(task), ())


if __name__ == "__main__":
    unittest.main()
