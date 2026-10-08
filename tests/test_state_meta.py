#!/usr/bin/env python3
"""Run state carries state_version and the manifest's meta, at run and task level.

Readers outside Ringer (for example Referee) link a work order to the planning
artifact it came from through `meta`, which Ringer copies into the run state
untouched. `state_version` lets a reader refuse a run state format it does not
know instead of misreading it.
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

from ringer import (
    META_MAX_BYTES,
    STATE_VERSION,
    EngineConfig,
    Manifest,
    StateWriter,
    TaskRuntime,
    TaskSpec,
)

OPENSPEC_RUN = {"openspec": {"root": "payments-api", "change": "add-sso-login", "plan_commit": "a41c09"}}
OPENSPEC_TASK = {
    "openspec": {
        "change": "add-sso-login",
        "task": "2.1",
        "scenarios": ["Password login for non-SSO org", "Password login blocked for SSO org"],
        "owns": ["src/auth/login.controller.ts"],
    }
}


def task_obj(**extra: object) -> dict[str, object]:
    return {"key": "2.1", "spec": "Do the thing described here in full.", "check": "true", **extra}


def manifest_obj(**extra: object) -> dict[str, object]:
    return {"run_name": "add-sso-login", "workdir": "/tmp/ringer-meta-test", "tasks": [task_obj()], **extra}


class MetaParsingTests(unittest.TestCase):
    def test_task_meta_is_kept(self) -> None:
        self.assertEqual(TaskSpec.from_obj(task_obj(meta=OPENSPEC_TASK)).meta, OPENSPEC_TASK)

    def test_task_without_meta_has_none(self) -> None:
        self.assertIsNone(TaskSpec.from_obj(task_obj()).meta)

    def test_task_meta_must_be_an_object(self) -> None:
        for bad in (["x"], "x", 3, True):
            with self.assertRaisesRegex(ValueError, "meta must be a JSON object"):
                TaskSpec.from_obj(task_obj(meta=bad))

    def test_task_meta_size_cap(self) -> None:
        big = {"note": "x" * META_MAX_BYTES}
        with self.assertRaisesRegex(ValueError, "meta is larger than"):
            TaskSpec.from_obj(task_obj(meta=big))

    def test_run_meta_is_kept(self) -> None:
        self.assertEqual(Manifest.from_obj(manifest_obj(meta=OPENSPEC_RUN)).meta, OPENSPEC_RUN)

    def test_run_meta_must_be_an_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "meta must be a JSON object"):
            Manifest.from_obj(manifest_obj(meta=["x"]))

    def test_run_meta_size_cap(self) -> None:
        with self.assertRaisesRegex(ValueError, "meta is larger than"):
            Manifest.from_obj(manifest_obj(meta={"note": "x" * META_MAX_BYTES}))

    def test_from_path_keeps_run_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(manifest_obj(meta=OPENSPEC_RUN)), encoding="utf-8")
            self.assertEqual(Manifest.from_path(path).meta, OPENSPEC_RUN)

    def test_meta_is_a_copy(self) -> None:
        raw = {"openspec": {"change": "c1"}}
        spec = TaskSpec.from_obj(task_obj(meta=raw))
        raw["openspec"]["change"] = "changed later"
        self.assertEqual(spec.meta, {"openspec": {"change": "c1"}})


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.state_dir = Path(self._temp.name)

    def _writer(self, task_meta: dict | None, run_meta: dict | None) -> StateWriter:
        taskdir = self.state_dir / "task"
        taskdir.mkdir(parents=True, exist_ok=True)
        log_path = taskdir / "worker.log"
        log_path.write_text("worker output\n", encoding="utf-8")
        runtime = TaskRuntime(
            task=TaskSpec(key="2.1", spec="Do the thing described here in full.", check="true", engine="mock", meta=task_meta),
            taskdir=taskdir,
            log_path=log_path,
            status="running",
            attempts=1,
            spec_short="do the thing",
        )
        runtime.started_at_monotonic = 1.0
        return StateWriter(
            "run-meta",
            "add-sso-login",
            "test-agent",
            self.state_dir,
            {"mock": EngineConfig(name="mock", bin=sys.executable, args_template=("-c", "pass"), full_access_args=(), sandbox_args=())},
            datetime(2026, 10, 8, tzinfo=timezone.utc),
            [runtime],
            threading.RLock(),
            meta=run_meta,
        )

    def test_state_version(self) -> None:
        self.assertEqual(STATE_VERSION, 1)
        self.assertEqual(self._writer(None, None).snapshot()["state_version"], 1)

    def test_meta_in_state(self) -> None:
        state = self._writer(OPENSPEC_TASK, OPENSPEC_RUN).snapshot()
        self.assertEqual(state["meta"], OPENSPEC_RUN)
        self.assertEqual(state["tasks"][0]["meta"], OPENSPEC_TASK)

    def test_no_meta_is_null(self) -> None:
        state = self._writer(None, None).snapshot()
        self.assertIsNone(state["meta"])
        self.assertIsNone(state["tasks"][0]["meta"])

    def test_written_file_has_both(self) -> None:
        writer = self._writer(OPENSPEC_TASK, OPENSPEC_RUN)
        writer.flush()
        data = json.loads(writer.path.read_text(encoding="utf-8"))
        self.assertEqual(data["state_version"], 1)
        self.assertEqual(data["meta"]["openspec"]["change"], "add-sso-login")
        self.assertEqual(data["tasks"][0]["meta"]["openspec"]["task"], "2.1")


if __name__ == "__main__":
    unittest.main()
