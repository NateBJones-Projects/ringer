#!/usr/bin/env python3
"""Nothing is dispatched until something has proved it could pass.

Measured on 2026-09-10, across one estate's runs: of $49 metered spend, 80%
was a single run restarted sixteen times, and its worst restart failed 31 of
36 tasks for $19.43 -- because the manifest told every worker to write a
deliverable to a path the sandbox forbids. Half of all metered spend went to
tasks that were retried and still failed. None of that needed a better model
or a cheaper one. It needed one question asked before dispatch: can this task
pass at all?

The controls that already existed -- a meter, a budget, an abort -- all fire
AFTER the spend. These two fire before it.

Pinned here, each provoked on purpose:
  * a dispatch with no baseline runs one first, and refuses on what it finds
  * a check that cannot fail is refused (it is green now, so it is green at
    the end, so it can never tell you the work happened)
  * a check that cannot be executed at all is refused
  * a canary whose verdict is bad holds the rest of the batch back
  * both escapes exist, demand a REASON, and announce themselves
  * the baseline verdict lands in the run record, so "was this checked?" is
    answerable later rather than remembered
  * the canary RESEMBLES the batch it gates -- it is not merely the first
    task -- and the run record says which task it was and what it spoke for
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOCK_WORKER = ROOT / "engines" / "mock_worker.py"


def toml_string(value: object) -> str:
    return json.dumps(str(value))


class ProveBeforeBuyTests(unittest.TestCase):
    """Every test here drives the real CLI end to end. A gate nobody has
    watched fail is a gate nobody should trust."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.state_dir = self.root / "state"
        self.workdir = self.root / "work"
        self.config_path = self.root / "config.toml"
        (self.root / "home").mkdir()
        (self.root / "rhome").mkdir()
        self.config_path.write_text(
            "\n".join(
                [
                    f"state_dir = {toml_string(self.state_dir)}",
                    "",
                    "[eval]",
                    'backend = "jsonl"',
                    f"jsonl_path = {toml_string(self.root / 'runs.jsonl')}",
                    "",
                    "[artifact]",
                    "enabled = false",
                    "",
                    "[engines.mock]",
                    f"bin = {toml_string(sys.executable)}",
                    "args_template = [",
                    f"  {toml_string(MOCK_WORKER)},",
                    '  "{spec}",',
                    "]",
                    "sandbox_args = []",
                    "full_access_args = []",
                    "",
                    # A second, identically-behaving engine. Identical on
                    # purpose: these tests are about which task is CHOSEN as
                    # the canary, so the two must differ only in name.
                    "[engines.other]",
                    f"bin = {toml_string(sys.executable)}",
                    "args_template = [",
                    f"  {toml_string(MOCK_WORKER)},",
                    '  "{spec}",',
                    "]",
                    "sandbox_args = []",
                    "full_access_args = []",
                    "",
                    "[engines.third]",
                    f"bin = {toml_string(sys.executable)}",
                    "args_template = [",
                    f"  {toml_string(MOCK_WORKER)},",
                    '  "{spec}",',
                    "]",
                    "sandbox_args = []",
                    "full_access_args = []",
                    "",
                    # An engine that genuinely takes a model, so tasks can
                    # differ by MODEL on one engine -- the shape of the real
                    # incident (one harness, two models, the weaker one first).
                    # {model} precedes {spec} because the mock worker reads
                    # argv[-1] as the spec, so the model arg is inert to it.
                    "[engines.mockm]",
                    f"bin = {toml_string(sys.executable)}",
                    'model_default = "base-model"',
                    "args_template = [",
                    f"  {toml_string(MOCK_WORKER)},",
                    '  "{model}",',
                    '  "{spec}",',
                    "]",
                    "sandbox_args = []",
                    "full_access_args = []",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._temp.cleanup()

    # ---- helpers -------------------------------------------------------

    def write_manifest(self, tasks: list[dict], **overrides: object) -> Path:
        manifest = {
            "run_name": "prove-before-buy",
            "workdir": str(self.workdir),
            "max_parallel": 2,
            "worktrees": False,
            "tasks": tasks,
        }
        manifest.update(overrides)
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return path

    def run_ringer(self, manifest: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            HOME=str(self.root / "home"),
            RINGER_HOME=str(self.root / "rhome"),
            XDG_CONFIG_HOME=str(self.root / "xdg"),
            RINGER_NO_SELF_UPDATE="1",
        )
        return subprocess.run(
            [
                sys.executable,
                "ringer.py",
                "run",
                str(manifest),
                "--config",
                str(self.config_path),
                "--no-dashboard",
                "--identity",
                "prove-before-buy-test",
                *extra,
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def writes(self, key: str, filename: str, body: str = "done") -> dict:
        """A mock task that really writes its deliverable, so its check passes."""
        return {
            "key": key,
            "engine": "mock",
            "task_type": "probe",
            "spec": (
                "You are the deterministic mock worker. Write only the file in "
                "the MOCK_FILE block.\n"
                f"MOCK_FILE: {filename}\n"
                f"{body}\n"
                "MOCK_END"
            ),
            "check": (
                f"test -f {filename} || {{ echo 'FAIL: {filename} was not created'; exit 1; }}"
            ),
            "expect_files": [filename],
            "verified": f"{filename} exists",
        }

    def writes_nothing(self, key: str, filename: str) -> dict:
        """A mock task whose worker fails, so its check fails honestly."""
        return {
            "key": key,
            "engine": "mock",
            "task_type": "probe",
            "spec": (
                "You are the deterministic mock worker. This task simulates a "
                "worker failure and leaves the check without its file.\n"
                "MOCK_FAIL"
            ),
            "check": (
                f"test -f {filename} || {{ echo 'FAIL: {filename} was not created'; exit 1; }}"
            ),
            "expect_files": [filename],
            "verified": f"{filename} exists",
            "max_attempts": 1,
        }

    def read_run_record(self) -> dict:
        runs = sorted((self.state_dir / "runs").glob("*.json"))
        self.assertTrue(runs, "no run record was written")
        return json.loads(runs[-1].read_text(encoding="utf-8"))

    # ---- refusal 1: a check that cannot fail ---------------------------

    def test_a_check_that_cannot_fail_is_refused_before_any_worker_spawns(self) -> None:
        manifest = self.write_manifest(
            [
                {
                    "key": "cannot-fail",
                    "engine": "mock",
                    "task_type": "probe",
                    "spec": "MOCK_FILE: irrelevant.txt\nx\nMOCK_END",
                    # Green now, therefore green at the end, therefore unable
                    # to distinguish work done from work not done.
                    "check": "echo checking; exit 0",
                    "verified": "nothing, and that is the point",
                },
                self.writes("honest", "honest.txt"),
            ]
        )

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        self.assertEqual(2, result.returncode, output)
        self.assertIn("DISPATCH REFUSED", output)
        self.assertIn("already pass against the unmodified tree", output)
        self.assertIn("cannot-fail", output)
        # The honest task must NOT be named as an objection: it fails baseline,
        # which is the wanted result.
        objection_lines = [line for line in output.splitlines() if line.strip().startswith("- ")]
        self.assertTrue(objection_lines, output)
        self.assertNotIn("honest", " ".join(objection_lines))
        # Nothing was bought.
        self.assertFalse(
            (self.state_dir / "runs").exists() and list((self.state_dir / "runs").glob("*.json")),
            "a refused dispatch must not create a run record",
        )

    # ---- refusal 2: a check that cannot be executed --------------------

    def test_a_check_that_cannot_be_executed_is_refused(self) -> None:
        # A key that escapes its scratch root cannot be given a taskdir, so
        # its check never runs -- the shape of "a deliverable no sandboxed
        # worker can write", caught before the workers are paid for.
        manifest = self.write_manifest(
            [
                {
                    "key": "../escape",
                    "engine": "mock",
                    "task_type": "probe",
                    "spec": "MOCK_FILE: out.txt\nx\nMOCK_END",
                    "check": "test -f out.txt || { echo 'FAIL: missing'; exit 1; }",
                    "expect_files": ["out.txt"],
                    "verified": "out.txt exists",
                }
            ]
        )

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        self.assertEqual(2, result.returncode, output)
        self.assertIn("DISPATCH REFUSED", output)
        self.assertIn("could not be executed at all", output)

    def test_a_deliverable_no_worker_could_write_is_refused(self) -> None:
        # The literal measured incident: a manifest that told every worker to
        # write its deliverable to a path nothing can create. Previously that
        # was invisible until after payment -- 31 of 36 tasks failing
        # identically, $19.43 on the worst restart alone.
        #
        # The parent is a regular FILE, so creating anything beneath it fails
        # with ENOTDIR on every platform. A read-only directory would not do:
        # CI often runs as root, where mode bits are ignored and the test
        # would silently stop proving anything.
        blocker = self.root / "blocker"
        blocker.write_text("I am a file, not a directory\n", encoding="utf-8")
        unreachable = blocker / "out.txt"

        manifest = self.write_manifest(
            [
                {
                    "key": "unreachable-deliverable",
                    "engine": "mock",
                    "task_type": "probe",
                    "spec": f"MOCK_FILE: {unreachable}\nx\nMOCK_END",
                    "check": f"test -f {unreachable} || {{ echo 'FAIL: missing'; exit 1; }}",
                    "expect_files": [str(unreachable)],
                    "verified": "the deliverable exists",
                },
                self.writes("honest", "honest.txt"),
            ]
        )

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        self.assertEqual(2, result.returncode, output)
        self.assertIn("DISPATCH REFUSED", output)
        self.assertIn("could not be executed at all", output)
        self.assertIn("unreachable-deliverable", output)
        self.assertIn("is not a directory", output)

    def test_a_relative_deliverable_is_never_called_unwritable(self) -> None:
        # The complement that keeps the check honest: a relative deliverable
        # lands in the task's own scratch dir, which the harness creates. If
        # this ever started reading as unwritable, the gate would refuse
        # essentially every well-formed manifest.
        manifest = self.write_manifest(
            [
                self.writes("first", "nested/dir/first.txt"),
                self.writes("second", "second.txt"),
            ]
        )

        result = self.run_ringer(manifest, "--no-canary", "exercising baseline only")
        output = result.stdout + result.stderr

        self.assertNotIn("DISPATCH REFUSED", output)
        self.assertNotIn("unwritable", output)

    # ---- refusal 3: a bad canary holds the batch -----------------------

    def test_a_bad_canary_verdict_holds_the_rest_of_the_batch(self) -> None:
        # The first task's worker fails. The other three must never spawn --
        # that is the whole saving, and it is why the canary is a STOP and not
        # merely a smaller batch.
        manifest = self.write_manifest(
            [
                self.writes_nothing("canary", "never.txt"),
                self.writes("second", "second.txt"),
                self.writes("third", "third.txt"),
                self.writes("fourth", "fourth.txt"),
            ]
        )

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("Canary: running 1 of 4 tasks", output)
        self.assertIn("RUN STOPPED", output)
        self.assertIn("did not pass its own check", output)

        record = self.read_run_record()
        by_key = {task["key"]: task for task in record["tasks"]}
        self.assertEqual("fail", by_key["canary"]["status"], record)
        for held in ("second", "third", "fourth"):
            self.assertEqual(
                "SKIPPED",
                by_key[held]["verdict"],
                f"{held} was dispatched despite a failed canary",
            )
        # The deliverables of the held tasks must not exist: nothing ran.
        for held_file in ("second.txt", "third.txt", "fourth.txt"):
            self.assertFalse(
                list(self.workdir.rglob(held_file)),
                f"{held_file} exists, so a held task was actually dispatched",
            )

    def test_a_good_canary_releases_the_batch(self) -> None:
        # The complement, and the more important of the pair: a gate that
        # blocks everything is not a gate, it is an outage.
        manifest = self.write_manifest(
            [
                self.writes("canary", "canary.txt"),
                self.writes("second", "second.txt"),
                self.writes("third", "third.txt"),
            ]
        )

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        self.assertEqual(0, result.returncode, output)
        self.assertIn("Canary: canary passed — releasing 2 task(s).", output)
        record = self.read_run_record()
        for task in record["tasks"]:
            self.assertEqual("pass", task["status"], f"{task['key']} did not pass")

    def test_a_single_task_run_skips_the_canary_and_says_so(self) -> None:
        # A run of one task IS its own canary; holding "the rest" back would
        # hold nothing. Announced, because silence here is indistinguishable
        # from the gate being off.
        manifest = self.write_manifest([self.writes("only", "only.txt")])

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        self.assertEqual(0, result.returncode, output)
        self.assertIn("Canary: skipped — a single-task run is its own canary", output)

    # ---- the canary must resemble the batch it gates --------------------

    def test_a_minority_first_task_does_not_gate_the_batch(self) -> None:
        # The measured incident (work#1044): a 32-task scout ran 31 tasks on
        # one model and task 1 on a weaker one left over from an audition. It
        # failed, 31 tasks were skipped, and the model they would have used had
        # passed the identical check first try minutes earlier.
        #
        # Here task 1 is the only task on `other`, and it is a task whose
        # worker fails. If it were still chosen, the batch would be held.
        manifest = self.write_manifest(
            [
                {**self.writes_nothing("odd-one-out", "never.txt"), "engine": "other"},
                self.writes("first", "first.txt"),
                self.writes("second", "second.txt"),
                self.writes("third", "third.txt"),
            ]
        )

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        # The canary moved off the minority task, and said so.
        self.assertIn("not odd-one-out", output)
        self.assertIn("Canary: first passed", output)

        record = self.read_run_record()
        sel = record["preflight"]["canary"]["selection"]
        self.assertEqual("first", sel["task"])
        self.assertEqual("mock", sel["engine"])
        self.assertEqual(3, sel["covers"])
        self.assertEqual(4, sel["of"])
        self.assertTrue(sel["representative"])
        self.assertEqual("odd-one-out", sel["moved_from"])

        # And the batch really was released: the minority task ran anyway,
        # and failed on its own merits rather than gating anything.
        by_key = {t["key"]: t for t in record["tasks"]}
        for released in ("second", "third"):
            self.assertEqual("pass", by_key[released]["status"], record)
        self.assertEqual("fail", by_key["odd-one-out"]["status"], record)

    def test_a_minority_MODEL_on_one_engine_does_not_gate_the_batch(self) -> None:
        # This is the measured incident's exact shape, and the half the
        # engine-based tests above cannot reach: ONE engine, two models, the
        # weaker one declared first. In the real run that weak task failed and
        # took 31 healthy tasks with it.
        #
        # Pairing on engine alone would pick the failing task here and hold
        # the batch, because all four tasks share an engine.
        manifest = self.write_manifest(
            [
                {
                    **self.writes_nothing("weak", "never.txt"),
                    "engine": "mockm",
                    "model": "weak-model",
                },
                {**self.writes("s1", "s1.txt"), "engine": "mockm", "model": "strong-model"},
                {**self.writes("s2", "s2.txt"), "engine": "mockm", "model": "strong-model"},
                {**self.writes("s3", "s3.txt"), "engine": "mockm", "model": "strong-model"},
            ]
        )

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        self.assertIn("not weak", output)
        self.assertIn("Canary: s1 passed", output)

        sel = self.read_run_record()["preflight"]["canary"]["selection"]
        self.assertEqual("s1", sel["task"])
        self.assertEqual("mockm", sel["engine"])
        self.assertEqual("strong-model", sel["model"])
        self.assertEqual(3, sel["covers"])
        self.assertEqual("weak", sel["moved_from"])
        self.assertTrue(sel["representative"])

        by_key = {t["key"]: t for t in self.read_run_record()["tasks"]}
        for released in ("s2", "s3"):
            self.assertEqual("pass", by_key[released]["status"])
        self.assertEqual("fail", by_key["weak"]["status"])

    def test_a_uniform_batch_keeps_the_first_task_as_canary(self) -> None:
        # The complement. When every task shares an engine/model there is
        # nothing to move to, and manifest order must be preserved — a
        # selection rule that reorders a uniform batch would be churn.
        manifest = self.write_manifest(
            [
                self.writes("alpha", "alpha.txt"),
                self.writes("beta", "beta.txt"),
                self.writes("gamma", "gamma.txt"),
            ]
        )

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        self.assertEqual(0, result.returncode, output)
        self.assertNotIn("not alpha", output)
        sel = self.read_run_record()["preflight"]["canary"]["selection"]
        self.assertEqual("alpha", sel["task"])
        self.assertEqual(3, sel["covers"])
        self.assertIsNone(sel["moved_from"])
        self.assertTrue(sel["representative"])

    def test_a_batch_with_no_majority_says_the_canary_is_a_weak_probe(self) -> None:
        # Two engines, two tasks each: whichever is picked speaks for half.
        # The gate still runs — one task's spend is cheap — but its verdict
        # must not be read as a statement about the batch, and the run says so
        # rather than leaving the operator to infer it.
        manifest = self.write_manifest(
            [
                self.writes("a1", "a1.txt"),
                {**self.writes("b1", "b1.txt"), "engine": "other"},
                self.writes("a2", "a2.txt"),
                {**self.writes("b2", "b2.txt"), "engine": "other"},
            ]
        )

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        self.assertEqual(0, result.returncode, output)
        self.assertIn("speaks for 2 of 4", output)
        sel = self.read_run_record()["preflight"]["canary"]["selection"]
        self.assertEqual(2, sel["covers"])
        self.assertEqual(4, sel["of"])
        # 2 of 4 is exactly half, which still counts as representative --
        # pinned so the boundary is a decision rather than an accident.
        self.assertTrue(sel["representative"])

    def test_the_dominant_pair_wins_even_when_it_is_not_first(self) -> None:
        # Majority rules over manifest order: `other` holds 3 of 5, so the
        # canary moves off the first task even though that task is perfectly
        # healthy. Nothing is wrong with a1 — it simply is not what most of
        # this batch will run as.
        manifest = self.write_manifest(
            [
                self.writes("a1", "a1.txt"),
                self.writes("a2", "a2.txt"),
                {**self.writes("b1", "b1.txt"), "engine": "other"},
                {**self.writes("b2", "b2.txt"), "engine": "other"},
                {**self.writes("b3", "b3.txt"), "engine": "other"},
            ],
            max_parallel=2,
        )

        result = self.run_ringer(manifest)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        sel = self.read_run_record()["preflight"]["canary"]["selection"]
        self.assertEqual("other", sel["engine"])
        self.assertEqual("b1", sel["task"])
        self.assertEqual(3, sel["covers"])
        self.assertTrue(sel["representative"])
        self.assertEqual("a1", sel["moved_from"])

    def test_a_canary_speaking_for_a_minority_warns(self) -> None:
        # A genuinely fragmented batch: three distinct engine/model pairs at
        # 2 / 2 / 1, so whichever is chosen speaks for 2 of 5 — under half.
        #
        # The gate still runs, because one task's spend is cheap. What it must
        # NOT do is let that verdict read as a statement about the batch.
        manifest = self.write_manifest(
            [
                self.writes("a1", "a1.txt"),
                self.writes("a2", "a2.txt"),
                {**self.writes("b1", "b1.txt"), "engine": "other"},
                {**self.writes("b2", "b2.txt"), "engine": "other"},
                {**self.writes("c1", "c1.txt"), "engine": "third"},
            ],
            max_parallel=2,
        )

        result = self.run_ringer(manifest)
        output = result.stdout + result.stderr

        self.assertEqual(0, result.returncode, output)
        self.assertIn("speaks for 2 of 5", output)
        self.assertIn("no majority engine/model", output)
        self.assertIn("not 'the batch is broken'", output)

        sel = self.read_run_record()["preflight"]["canary"]["selection"]
        self.assertEqual(2, sel["covers"])
        self.assertEqual(5, sel["of"])
        self.assertFalse(sel["representative"])

    # ---- the escapes ---------------------------------------------------

    def test_the_baseline_waiver_announces_itself_and_is_recorded(self) -> None:
        manifest = self.write_manifest(
            [
                self.writes("first", "first.txt"),
                self.writes("second", "second.txt"),
            ]
        )

        result = self.run_ringer(
            manifest, "--no-baseline", "pinning the waiver path in a test"
        )
        output = result.stdout + result.stderr

        self.assertEqual(0, result.returncode, output)
        self.assertIn("BASELINE WAIVED (not proved, not verified)", output)
        self.assertIn("pinning the waiver path in a test", output)
        self.assertNotIn("Baseline: executing", output)

        record = self.read_run_record()
        baseline = record["preflight"]["baseline"]
        self.assertFalse(baseline["ran"])
        self.assertEqual("pinning the waiver path in a test", baseline["skipped_reason"])

    def test_the_canary_waiver_announces_itself_and_is_recorded(self) -> None:
        manifest = self.write_manifest(
            [
                self.writes_nothing("would-be-canary", "never.txt"),
                self.writes("second", "second.txt"),
            ]
        )

        result = self.run_ringer(manifest, "--no-canary", "tasks are independent")
        output = result.stdout + result.stderr

        self.assertIn("CANARY WAIVED (not proved, not verified)", output)
        self.assertIn("tasks are independent", output)
        self.assertNotIn("Canary: running 1 of", output)

        # With the canary waived the second task really is dispatched, even
        # though the first failed -- which is exactly what the waiver buys,
        # and exactly what it costs.
        record = self.read_run_record()
        by_key = {task["key"]: task for task in record["tasks"]}
        self.assertEqual("pass", by_key["second"]["status"], record)
        self.assertFalse(record["preflight"]["canary"]["enabled"])
        self.assertEqual(
            "tasks are independent", record["preflight"]["canary"]["skipped_reason"]
        )

    def test_a_waiver_without_a_reason_is_rejected(self) -> None:
        # A bypass that leaves no trace is the same as no gate. An empty
        # reason must not quietly re-enable the gate either -- the operator
        # believes they turned it off, so say so instead.
        manifest = self.write_manifest([self.writes("only", "only.txt")])

        result = self.run_ringer(manifest, "--no-baseline", "   ")
        output = result.stdout + result.stderr

        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("--no-baseline requires a reason", output)

    # ---- the record ----------------------------------------------------

    def test_the_baseline_verdict_lands_in_the_run_record(self) -> None:
        manifest = self.write_manifest(
            [
                self.writes("first", "first.txt"),
                self.writes("second", "second.txt"),
            ]
        )

        result = self.run_ringer(manifest)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

        record = self.read_run_record()
        baseline = record["preflight"]["baseline"]
        self.assertTrue(baseline["ran"])
        self.assertEqual(2, baseline["total"])
        # Both checks demand a file no worker has written yet: both must fail
        # baseline, and neither is an objection.
        self.assertEqual(2, baseline["fail"])
        self.assertEqual(0, baseline["pass"])
        self.assertEqual(0, baseline["error"])
        self.assertEqual([], baseline["objections"])
        self.assertEqual(
            {"first", "second"}, {task["key"] for task in baseline["tasks"]}
        )

        canary = record["preflight"]["canary"]
        self.assertTrue(canary["enabled"])
        self.assertIsNone(canary["skipped_reason"])
        self.assertFalse(canary["human_confirm"])
        # "Was a canary configured?" and "was it judged, and what did it say?"
        # are different questions, and only the second one says whether the
        # batch was released on evidence.
        self.assertEqual("released", canary["verdict"]["outcome"])
        self.assertEqual("first", canary["verdict"]["task"])
        self.assertEqual(1, canary["verdict"]["held_back"])

    def test_a_held_batch_records_why_it_was_held(self) -> None:
        manifest = self.write_manifest(
            [
                self.writes_nothing("canary", "never.txt"),
                self.writes("second", "second.txt"),
            ]
        )

        result = self.run_ringer(manifest)
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)

        verdict = self.read_run_record()["preflight"]["canary"]["verdict"]
        self.assertEqual("held", verdict["outcome"])
        self.assertEqual("canary", verdict["task"])
        self.assertIn("did not pass its own check", verdict["reason"])


if __name__ == "__main__":
    unittest.main()
