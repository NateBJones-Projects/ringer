#!/usr/bin/env python3
"""depends_on: task ordering, skip propagation, and hard manifest validation.

Covers the required semantics: parsing of the field (including every
rejection), graph validation (unknown keys, self-dependency, cycles), the
dependency wait happening before a max_parallel slot is taken, skip
propagation through a chain and through fan-in, the retry boundary (a
prerequisite is terminal only after its final attempt), and the guarantee
that a skipped task launches no worker, consumes no attempt, and writes no
eval row. Uses only deterministic, non-model workers.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402

from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EngineConfig,
    EvalConfig,
    Manifest,
    RingerRunner,
    SteeringConfig,
    TaskSpec,
)


PASS_SPEC = "MOCK_FILE: out.txt\ndone\nMOCK_END"
PASS_CHECK = 'test "$(cat out.txt 2>/dev/null)" = done'
FAIL_SPEC = "MOCK_FAIL"
FAIL_CHECK = 'test "$(cat out.txt 2>/dev/null)" = done'


def task_obj(key: str, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "key": key,
        "spec": "Do the work described here in full.",
        "check": "true",
    }
    data.update(overrides)
    return data


def manifest_obj(
    *tasks: dict[str, object],
    run_name: str = "deps",
    workdir: Path | None = None,
    max_parallel: int = 2,
) -> dict[str, object]:
    return {
        "run_name": run_name,
        "workdir": str(workdir),
        "max_parallel": max_parallel,
        "tasks": list(tasks),
    }


def mock_engine() -> EngineConfig:
    return EngineConfig(
        name="mock",
        bin=sys.executable,
        args_template=(str(ROOT / "engines" / "mock_worker.py"), "{spec}"),
        full_access_args=(),
        sandbox_args=(),
        token_regex=None,
    )


def make_config(root: Path) -> AppConfig:
    return AppConfig(
        path=None,
        identity_default=None,
        state_dir=root / "state",
        dashboard_port_base=8787,
        hud_port=8700,
        hud_app_path=None,
        allow_full_access=False,
        eval=EvalConfig(backend="jsonl", jsonl_path=root / "eval.jsonl"),
        engines={"mock": mock_engine()},
        artifact=ArtifactConfig(
            enabled=False,
            out_template=str(root / "live.html"),
            report_template=str(root / "report.html"),
            index_out=root / "index.html",
        ),
        steering=SteeringConfig(dir=None),
    )


class TaskSpecDependsOnParsingTests(unittest.TestCase):
    def base(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "key": "a",
            "spec": "Do the work.",
            "check": "true",
        }
        data.update(overrides)
        return data

    def test_absent_depends_on_defaults_to_empty(self) -> None:
        self.assertEqual((), TaskSpec.from_obj(self.base()).depends_on)

    def test_depends_on_accepts_a_list_of_strings(self) -> None:
        task = TaskSpec.from_obj(self.base(depends_on=["producer", "lint"]))
        self.assertEqual(("producer", "lint"), task.depends_on)

    def test_depends_on_trims_whitespace_around_keys(self) -> None:
        task = TaskSpec.from_obj(self.base(depends_on=["  producer  "]))
        self.assertEqual(("producer",), task.depends_on)

    def test_bare_string_depends_on_is_rejected_never_coerced(self) -> None:
        with self.assertRaisesRegex(ValueError, "depends_on must be a JSON list"):
            TaskSpec.from_obj(self.base(depends_on="producer"))

    def test_dict_depends_on_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "depends_on must be a JSON list"):
            TaskSpec.from_obj(self.base(depends_on={"producer": "lint"}))

    def test_non_string_entry_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "depends_on entries must be strings"):
            TaskSpec.from_obj(self.base(depends_on=["producer", 7]))

    def test_duplicate_entries_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "more than once"):
            TaskSpec.from_obj(self.base(depends_on=["producer", "producer"]))

    def test_empty_after_trim_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            TaskSpec.from_obj(self.base(depends_on=["producer", "   "]))


class ManifestDependencyGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ringer-deps-graph-")
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name) / "work"

    def build(self, *tasks: dict[str, object]) -> Manifest:
        return Manifest.from_obj(manifest_obj(*tasks, workdir=self.workdir))

    def test_unknown_dependency_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "references unknown task 'ghost'"):
            self.build(task_obj("a", depends_on=["ghost"]))

    def test_self_dependency_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot depend on itself"):
            self.build(task_obj("a", depends_on=["a"]))

    def test_direct_cycle_is_rejected_with_cycle_keys_named(self) -> None:
        with self.assertRaisesRegex(ValueError, "dependency cycle: build -> qc -> build"):
            self.build(
                task_obj("build", depends_on=["qc"]),
                task_obj("qc", depends_on=["build"]),
            )

    def test_indirect_cycle_is_rejected_with_cycle_keys_named(self) -> None:
        with self.assertRaisesRegex(ValueError, "dependency cycle: a -> b -> c -> a"):
            self.build(
                task_obj("a", depends_on=["b"]),
                task_obj("b", depends_on=["c"]),
                task_obj("c", depends_on=["a"]),
            )

    def test_acyclic_diamond_is_accepted(self) -> None:
        manifest = self.build(
            task_obj("root"),
            task_obj("left", depends_on=["root"]),
            task_obj("right", depends_on=["root"]),
            task_obj("merge", depends_on=["left", "right"]),
        )
        self.assertEqual(4, len(manifest.tasks))
        self.assertEqual(("root",), manifest.tasks[1].depends_on)


class DependencyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ringer-deps-run-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.config = make_config(self.root)

    async def run_manifest(self, obj: dict[str, object], *, timeout: float = 30) -> RingerRunner:
        runner = RingerRunner(Manifest.from_obj(obj), self.config, "test", dashboard_enabled=False)
        await asyncio.wait_for(runner.run(), timeout=timeout)
        return runner

    def eval_rows(self) -> list[dict[str, object]]:
        path = self.config.eval.jsonl_path
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    async def test_dependent_waits_before_taking_a_concurrency_slot(self) -> None:
        # max_parallel 1: if the dependent held the only slot while waiting,
        # its prerequisite could never run and the run would deadlock.
        runner = await self.run_manifest(            manifest_obj(
                task_obj("producer", engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                task_obj("consumer", depends_on=["producer"], engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                run_name="two-stage", workdir=self.root / "work", max_parallel=1,
            ),
            timeout=30,
        )
        by_key = {runtime.task.key: runtime for runtime in runner.runtimes}
        self.assertEqual("pass", by_key["producer"].status)
        self.assertEqual("pass", by_key["consumer"].status)
        self.assertEqual(1, by_key["consumer"].attempts)
        self.assertTrue(all(future.done() for future in runner._task_results.values()))

    async def test_skip_propagates_through_a_chain_with_no_worker_no_attempt_no_eval_row(self) -> None:
        runner = await self.run_manifest(
            manifest_obj(
                task_obj("a", engine="mock", spec=FAIL_SPEC, expect_files=["out.txt"], check=FAIL_CHECK),
                task_obj("b", depends_on=["a"], engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                task_obj("c", depends_on=["b"], engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                run_name="skip-chain", workdir=self.root / "work", max_parallel=1,
            ),
            timeout=30,
        )
        by_key = {runtime.task.key: runtime for runtime in runner.runtimes}
        self.assertEqual("fail", by_key["a"].status)
        self.assertEqual("skipped", by_key["b"].status)
        self.assertEqual("skipped", by_key["c"].status)
        self.assertEqual(0, by_key["b"].attempts)
        self.assertEqual(0, by_key["c"].attempts)
        self.assertIsNone(by_key["b"].worker_pid)
        self.assertIsNone(by_key["c"].worker_pid)
        self.assertEqual(("a",), by_key["b"].blocked_by)
        self.assertEqual(("b",), by_key["c"].blocked_by)
        rows = self.eval_rows()
        self.assertEqual(["a", "a"], [row["task_key"] for row in rows])
        self.assertEqual({"fail", "skipped"}, {future.result() for future in runner._task_results.values()})
        self.assertTrue(all(future.done() for future in runner._task_results.values()))

    async def test_fan_in_waits_for_every_prerequisite(self) -> None:
        # Each prerequisite stamps a shared order log inside its check; the
        # merge must start only after both have stamped, so its stamp is later
        # than either's. p2's check sleeps so that, if the scheduler ignored
        # depends_on and ran the merge concurrently, the merge would finish and
        # stamp well before p2 — the ordering assertions below would then fail.
        log = self.root / "order.log"

        def stamp_check(key: str, *, delay: float = 0) -> str:
            check = (
                f"printf '%s %s\\n' '{key}' \"$(date +%s%N)\" >> {shlex.quote(str(log))}; "
                'test "$(cat out.txt 2>/dev/null)" = done'
            )
            return f"sleep {delay}; {check}" if delay else check

        runner = await self.run_manifest(
            manifest_obj(
                task_obj("p1", engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=stamp_check("p1")),
                task_obj("p2", engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=stamp_check("p2", delay=1)),
                task_obj("merge", depends_on=["p1", "p2"], engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=stamp_check("merge")),
                run_name="fan-in", workdir=self.root / "work", max_parallel=2,
            ),
            timeout=30,
        )
        self.assertTrue(all(runtime.status == "pass" for runtime in runner.runtimes))
        self.assertTrue(all(future.done() for future in runner._task_results.values()))
        stamps: dict[str, int] = {}
        for line in log.read_text(encoding="utf-8").splitlines():
            key, value = line.split(" ", 1)
            stamps[key] = int(value)
        self.assertEqual({"p1", "p2", "merge"}, set(stamps))
        self.assertGreater(stamps["merge"], stamps["p1"])
        self.assertGreater(stamps["merge"], stamps["p2"])

    async def test_fan_in_skips_when_any_prerequisite_fails(self) -> None:
        runner = await self.run_manifest(
            manifest_obj(
                task_obj("p1", engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                task_obj("p2", engine="mock", spec=FAIL_SPEC, expect_files=["out.txt"], check=FAIL_CHECK),
                task_obj("merge", depends_on=["p1", "p2"], engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                run_name="fan-in-fail", workdir=self.root / "work", max_parallel=2,
            ),
            timeout=30,
        )
        by_key = {runtime.task.key: runtime for runtime in runner.runtimes}
        self.assertEqual("pass", by_key["p1"].status)
        self.assertEqual("fail", by_key["p2"].status)
        self.assertEqual("skipped", by_key["merge"].status)
        self.assertEqual(0, by_key["merge"].attempts)
        self.assertEqual(("p2",), by_key["merge"].blocked_by)
        rows = sorted(
            (row["task_key"], row["verdict"]) for row in self.eval_rows()
        )
        self.assertEqual([("p1", "PASS"), ("p2", "FAIL"), ("p2", "FAIL")], rows)

    async def test_dependent_runs_only_after_prerequisite_final_attempt(self) -> None:
        # The producer fails attempt 1 and passes attempt 2; its dependent
        # must not start until the retry resolves, then it runs.
        runner = await self.run_manifest(
            manifest_obj(
                task_obj(
                    "prod",
                    engine="mock",
                    spec=PASS_SPEC,
                    expect_files=["out.txt"],
                    check="if [ -f second ]; then exit 0; else touch second; exit 1; fi",
                ),
                task_obj("qc", depends_on=["prod"], engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                run_name="retry-boundary", workdir=self.root / "work", max_parallel=1,
            ),
            timeout=30,
        )
        by_key = {runtime.task.key: runtime for runtime in runner.runtimes}
        self.assertEqual("pass", by_key["prod"].status)
        self.assertEqual(2, by_key["prod"].attempts)
        self.assertEqual("pass", by_key["qc"].status)
        self.assertEqual(1, by_key["qc"].attempts)
        self.assertEqual(
            ["prod", "prod", "qc"],
            [row["task_key"] for row in self.eval_rows()],
        )

    async def test_task_without_depends_on_behaves_exactly_as_before(self) -> None:
        runner = await self.run_manifest(
            manifest_obj(
                task_obj("solo", engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                run_name="solo", workdir=self.root / "work", max_parallel=1,
            ),
            timeout=30,
        )
        self.assertEqual("pass", runner.runtimes[0].status)
        self.assertEqual(1, runner.runtimes[0].attempts)
        self.assertEqual((), runner.runtimes[0].blocked_by)

    async def test_skipped_tasks_leave_no_taskdir_and_no_log(self) -> None:
        runner = await self.run_manifest(
            manifest_obj(
                task_obj("a", engine="mock", spec=FAIL_SPEC, expect_files=["out.txt"], check=FAIL_CHECK),
                task_obj("b", depends_on=["a"], engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                run_name="no-artifacts", workdir=self.root / "work", max_parallel=1,
            ),
            timeout=30,
        )
        by_key = {runtime.task.key: runtime for runtime in runner.runtimes}
        self.assertFalse(by_key["b"].taskdir.exists())
        self.assertFalse(by_key["b"].log_path.exists())

    async def test_exception_in_prerequisite_still_publishes_and_unblocks_dependent(self) -> None:
        runner = RingerRunner(
            Manifest.from_obj(
                manifest_obj(
                    task_obj("boom", engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                    task_obj("dep", depends_on=["boom"], engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                    run_name="boom", workdir=self.root / "work", max_parallel=1,
                )
            ),
            self.config,
            "test",
            dashboard_enabled=False,
        )

        async def boom_prepare(runtime: object) -> tuple[bool, str | None]:
            raise RuntimeError("simulated preparation explosion")

        runner._prepare_taskdir = boom_prepare  # type: ignore[method-assign]
        boom_task = asyncio.create_task(runner._run_task(runner.runtimes[0]))
        dep_task = asyncio.create_task(runner._run_task(runner.runtimes[1]))
        with self.assertRaises(RuntimeError):
            await boom_task
        await asyncio.wait_for(dep_task, timeout=10)
        self.assertTrue(runner._task_results["boom"].done())
        self.assertTrue(runner._task_results["dep"].done())
        self.assertEqual("fail", runner._task_results["boom"].result())
        self.assertEqual("skipped", runner.runtimes[1].status)

    async def test_cancelled_waiter_still_publishes_its_result(self) -> None:
        runner = RingerRunner(
            Manifest.from_obj(
                manifest_obj(
                    task_obj("never", engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                    task_obj("waiter", depends_on=["never"], engine="mock", spec=PASS_SPEC, expect_files=["out.txt"], check=PASS_CHECK),
                    run_name="cancel", workdir=self.root / "work", max_parallel=1,
                )
            ),
            self.config,
            "test",
            dashboard_enabled=False,
        )
        waiter_task = asyncio.create_task(runner._run_task(runner.runtimes[1]))
        await asyncio.sleep(0.05)
        waiter_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter_task
        self.assertTrue(runner._task_results["waiter"].done())
        self.assertEqual("fail", runner._task_results["waiter"].result())


class FinalBriefingHtmlTests(unittest.TestCase):
    def state(self, *statuses: str) -> dict[str, object]:
        return {
            "elapsed_s": 10,
            "tasks": [
                {"key": f"task-{index}", "status": status}
                for index, status in enumerate(statuses)
            ],
        }

    def test_final_briefing_names_pass_fail_and_skip_counts(self) -> None:
        html = ringer.final_briefing_html(self.state("pass", "fail", "skipped"))
        self.assertEqual(
            "Ringer finished 3 tasks in 10s. "
            '<span class="n-pass">1 finished and checked</span>, '
            '<span class="n-fail">1 failed after retry</span>, '
            "1 skipped.",
            html,
        )


class DependencyCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ringer-deps-cli-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.config_path = self.root / "config.toml"
        self.jsonl_path = self.root / "runs.jsonl"
        self.state_dir = self.root / "state"
        self.write_config()

    def write_config(self) -> None:
        engines = {
            "write_done": ["-c", "printf done > out.txt"],
            "write_wrong": ["-c", "printf wrong > out.txt"],
        }
        lines = [
            f'state_dir = "{self.state_dir}"',
            "dashboard_port_base = 18787",
            "allow_full_access = false",
            "",
            "[eval]",
            'backend = "jsonl"',
            f'jsonl_path = "{self.jsonl_path}"',
            "",
        ]
        for name, args_template in engines.items():
            lines.extend(
                [
                    f"[engines.{name}]",
                    'bin = "/bin/sh"',
                    f"args_template = {json.dumps(args_template)}",
                    "sandbox_args = []",
                    "full_access_args = []",
                    "token_regex = \"\"",
                    "",
                ]
            )
        self.config_path.write_text("\n".join(lines), encoding="utf-8")

    def run_cli(self, manifest: Path) -> subprocess.CompletedProcess[str]:
        cmd = [
            sys.executable,
            "-B",
            str(ROOT / "ringer.py"),
            "--config",
            str(self.config_path),
            "run",
            str(manifest),
            "--no-dashboard",
            "--identity",
            "test-runner",
        ]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["RINGER_NO_SELF_UPDATE"] = "1"
        return subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )

    def write_manifest(self, name: str, data: dict[str, object]) -> Path:
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def final_state(self) -> dict[str, object]:
        state_files = sorted((self.state_dir / "runs").glob("*.json"))
        self.assertEqual(1, len(state_files))
        return json.loads(state_files[0].read_text(encoding="utf-8"))

    def test_serialized_tasks_expose_depends_on_and_blocked_by(self) -> None:
        path = self.write_manifest(
            "two-stage",
            {
                "run_name": "two-stage",
                "workdir": str(self.root / "work"),
                "max_parallel": 1,
                "tasks": [
                    {
                        "key": "producer",
                        "engine": "write_done",
                        "spec": "Produce the artifact.",
                        "expect_files": ["out.txt"],
                        "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                    },
                    {
                        "key": "qc",
                        "depends_on": ["producer"],
                        "engine": "write_done",
                        "spec": "QC the artifact.",
                        "expect_files": ["out.txt"],
                        "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                    },
                ],
            },
        )
        result = self.run_cli(path)
        self.assertEqual(0, result.returncode, result.stdout)
        state = self.final_state()
        tasks = {task["key"]: task for task in state["tasks"]}
        self.assertEqual(["producer"], tasks["qc"]["depends_on"])
        self.assertEqual([], tasks["qc"]["blocked_by"])
        self.assertEqual([], tasks["producer"]["depends_on"])
        self.assertEqual([], tasks["producer"]["blocked_by"])
        self.assertEqual(2, state["totals"]["done"])
        self.assertEqual(0, state["totals"]["skipped"])

    def test_skipped_chain_is_counted_separately_in_summary_and_state(self) -> None:
        path = self.write_manifest(
            "skip-chain",
            {
                "run_name": "skip-chain",
                "workdir": str(self.root / "work"),
                "max_parallel": 1,
                "tasks": [
                    {
                        "key": "a",
                        "engine": "write_wrong",
                        "spec": "Produce the wrong artifact.",
                        "expect_files": ["out.txt"],
                        "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                    },
                    {
                        "key": "b",
                        "depends_on": ["a"],
                        "engine": "write_done",
                        "spec": "Depends on a.",
                        "expect_files": ["out.txt"],
                        "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                    },
                    {
                        "key": "c",
                        "depends_on": ["b"],
                        "engine": "write_done",
                        "spec": "Depends on b.",
                        "expect_files": ["out.txt"],
                        "check": 'test "$(cat out.txt 2>/dev/null)" = done',
                    },
                ],
            },
        )
        result = self.run_cli(path)
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("skipped: 2 task(s) did not run because a prerequisite did not pass", result.stdout)
        state = self.final_state()
        tasks = {task["key"]: task for task in state["tasks"]}
        self.assertEqual("fail", tasks["a"]["status"])
        self.assertEqual("skipped", tasks["b"]["status"])
        self.assertEqual("skipped", tasks["c"]["status"])
        self.assertEqual(["a"], tasks["b"]["depends_on"])
        self.assertEqual(["a"], tasks["b"]["blocked_by"])
        self.assertEqual(["b"], tasks["c"]["blocked_by"])
        self.assertEqual(0, tasks["b"]["attempts"])
        self.assertEqual(0, tasks["c"]["attempts"])
        self.assertEqual(1, state["summary"]["fail"])
        self.assertEqual(2, state["summary"]["skipped"])
        self.assertEqual(2, state["totals"]["skipped"])
        self.assertEqual(3, state["totals"]["done"])

    def test_invalid_manifests_exit_with_usage_error(self) -> None:
        cases = [
            (
                task_obj("a", depends_on="producer"),
                "depends_on must be a JSON list",
            ),
            (
                task_obj("a", depends_on=["ghost"]),
                "references unknown task 'ghost'",
            ),
            (
                task_obj("a", depends_on=["a"]),
                "cannot depend on itself",
            ),
        ]
        for index, (task, message) in enumerate(cases):
            with self.subTest(message=message):
                path = self.write_manifest(
                    f"invalid-{index}",
                    {
                        "run_name": f"invalid-{index}",
                        "workdir": str(self.root / "work"),
                        "max_parallel": 1,
                        "tasks": [task],
                    },
                )
                result = self.run_cli(path)
                self.assertEqual(2, result.returncode, result.stdout)
                self.assertIn(message, result.stdout)

    def test_cyclic_manifest_names_the_cycle_keys(self) -> None:
        path = self.write_manifest(
            "cycle",
            {
                "run_name": "cycle",
                "workdir": str(self.root / "work"),
                "max_parallel": 1,
                "tasks": [
                    {"key": "build", "spec": "Build.", "check": "true", "depends_on": ["qc"]},
                    {"key": "qc", "spec": "QC.", "check": "true", "depends_on": ["build"]},
                ],
            },
        )
        result = self.run_cli(path)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("dependency cycle: build -> qc -> build", result.stdout)


if __name__ == "__main__":
    unittest.main()
