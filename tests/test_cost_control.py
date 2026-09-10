"""Cost accounting and the two controls that stop a runaway run.

Every gate here is provoked on purpose. A cap nobody has watched fire is a cap
nobody should trust, and these exist because a $41.71 swarm reported as roughly
$3 and was restarted sixteen times.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ringer  # noqa: E402


def step(cost=None, total=0):
    part = {"type": "step-finish", "tokens": {"total": total}}
    if cost is not None:
        part["cost"] = cost
    return json.dumps({"type": "step_finish", "part": part})


class ParseStepCostsTests(unittest.TestCase):
    def _log(self, lines):
        tmp = Path(tempfile.mkdtemp()) / "worker.log"
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return tmp

    def test_sums_every_step_not_just_one(self):
        # The defect this replaces: a single step was recorded as the task's
        # cost. Three steps here, and the answer must be their sum.
        log = self._log([
            "[ringer.py] attempt 1 started",
            step(cost=0.01, total=100),
            step(cost=0.02, total=900),
            step(cost=0.03, total=50),
        ])
        usd, steps = ringer.parse_step_costs(log)
        self.assertAlmostEqual(usd, 0.06)
        self.assertEqual(steps, 3)
        # and emphatically not the largest single step
        self.assertNotAlmostEqual(usd, 0.03)

    def test_costs_accumulate_across_attempts(self):
        log = self._log([step(cost=0.05), "[ringer.py] attempt 2 started", step(cost=0.07)])
        usd, steps = ringer.parse_step_costs(log)
        self.assertAlmostEqual(usd, 0.12)
        self.assertEqual(steps, 2)

    def test_engine_reporting_no_cost_is_none_not_zero(self):
        # A plan-billed engine that reports nothing must not read as free.
        log = self._log([step(total=500), step(total=700)])
        usd, steps = ringer.parse_step_costs(log)
        self.assertIsNone(usd)
        self.assertEqual(steps, 2)

    def test_a_genuinely_free_model_is_zero_not_none(self):
        log = self._log([step(cost=0.0), step(cost=0.0)])
        usd, _ = ringer.parse_step_costs(log)
        self.assertEqual(usd, 0.0)

    def test_survives_noise_and_a_missing_file(self):
        log = self._log(["not json at all", "{broken", step(cost=0.25)])
        usd, steps = ringer.parse_step_costs(log)
        self.assertAlmostEqual(usd, 0.25)
        self.assertEqual(steps, 1)
        usd, steps = ringer.parse_step_costs(Path("/nonexistent/worker.log"))
        self.assertIsNone(usd)
        self.assertEqual(steps, 0)


def manifest_obj(**extra):
    obj = {
        "run_name": "cost-tests",
        "workdir": tempfile.mkdtemp(),
        "max_parallel": 1,
        "tasks": [{
            "key": "t1",
            "spec": "do a thing",
            "check": "echo checking; test -f out.txt",
            "verified": "out.txt exists",
        }],
    }
    obj.update(extra)
    return obj


class ManifestBudgetTests(unittest.TestCase):
    def test_budget_and_abort_round_trip(self):
        m = ringer.Manifest.from_obj(manifest_obj(budget_usd=6.5, abort_after_repeated_failures=3))
        self.assertAlmostEqual(m.budget_usd, 6.5)
        self.assertEqual(m.abort_after_repeated_failures, 3)
        # and survive the copy that --max-parallel makes
        self.assertAlmostEqual(m.with_max_parallel(4).budget_usd, 6.5)
        self.assertEqual(m.with_max_parallel(4).abort_after_repeated_failures, 3)

    def test_absent_means_unlimited(self):
        m = ringer.Manifest.from_obj(manifest_obj())
        self.assertIsNone(m.budget_usd)
        self.assertIsNone(m.abort_after_repeated_failures)

    def test_nonpositive_values_are_refused(self):
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                ringer.Manifest.from_obj(manifest_obj(budget_usd=bad))
            with self.assertRaises(ValueError):
                ringer.Manifest.from_obj(manifest_obj(abort_after_repeated_failures=bad))


class UnwritableDeliverableLintTests(unittest.TestCase):
    """The bug that cost the most: a deliverable the worker cannot write."""

    def _manifest(self, spec, expect, worktrees=True):
        workdir = tempfile.mkdtemp()
        return ringer.Manifest.from_obj({
            "run_name": "lint-tests",
            "workdir": workdir,
            "max_parallel": 1,
            "worktrees": worktrees,
            "repo": None,
            "tasks": [{
                "key": "scout1",
                "spec": spec,
                "check": f"echo verifying; test -s {expect}",
                "verified": "report exists",
                "expect_files": [expect],
            }],
        }), workdir

    def test_spec_ordering_a_write_outside_the_sandbox_is_flagged(self):
        outside = "/tmp/somewhere-else/report.json"
        m, _ = self._manifest(f"Write your report to {outside} when done.", outside)
        findings = ringer.lint_manifest(m)
        self.assertTrue(
            any("sandbox forbids" in f for f in findings),
            f"expected an unwritable-path finding, got: {findings}",
        )

    def test_the_check_exporting_to_an_absolute_path_is_fine(self):
        # The fix-swarm pattern: the CHECK writes the patch out of the worktree.
        # The spec never names that path, so the worker is asked for nothing
        # impossible and this must NOT be flagged.
        outside = "/tmp/somewhere-else/task.patch"
        m, _ = self._manifest("Leave your changes uncommitted in the worktree.", outside)
        findings = ringer.lint_manifest(m)
        self.assertFalse(
            any("sandbox forbids" in f for f in findings),
            f"check-exported deliverable must not be flagged, got: {findings}",
        )

    def test_a_path_inside_the_task_directory_is_fine(self):
        workdir = tempfile.mkdtemp()
        inside = str(Path(workdir) / "scout1" / "report.json")
        m = ringer.Manifest.from_obj({
            "run_name": "lint-tests",
            "workdir": workdir,
            "max_parallel": 1,
            "worktrees": True,
            "repo": None,
            "tasks": [{
                "key": "scout1",
                "spec": f"Write {inside}",
                "check": f"echo verifying; test -s {inside}",
                "verified": "report exists",
                "expect_files": [inside],
            }],
        })
        findings = ringer.lint_manifest(m)
        self.assertFalse(any("sandbox forbids" in f for f in findings), findings)


if __name__ == "__main__":
    unittest.main()


class TicketAttributionTests(unittest.TestCase):
    """Cost you cannot attribute to a requirement cannot be steered."""

    def _m(self, **task_extra):
        task = {"key": "t1", "spec": "do it", "check": "echo checking; test -f out",
                "verified": "out exists"}
        task.update(task_extra)
        return ringer.Manifest.from_obj({
            "run_name": "ticket-tests", "workdir": tempfile.mkdtemp(),
            "max_parallel": 1, "tasks": [task]})

    def test_product_work_without_a_ticket_is_flagged(self):
        findings = ringer.lint_manifest(self._m(task_type="code-fix"))
        self.assertTrue(any("names no ticket" in f for f in findings), findings)

    def test_product_work_with_a_ticket_is_not_flagged(self):
        findings = ringer.lint_manifest(self._m(task_type="code-fix", ticket="work#666"))
        self.assertFalse(any("names no ticket" in f for f in findings), findings)

    def test_a_bakeoff_or_probe_needs_no_ticket(self):
        # Not every run serves a requirement; the rule must be scoped or it
        # becomes noise everyone learns to ignore.
        for tt in ("research", "probe", "code-review", "bakeoff"):
            findings = ringer.lint_manifest(self._m(task_type=tt))
            self.assertFalse(any("names no ticket" in f for f in findings), f"{tt}: {findings}")


class UncostedEngineTests(unittest.TestCase):
    """Codex bills elsewhere. Its work must not read as free."""

    def _engine(self, **kw):
        base = dict(name="codex", bin="/bin/true", args_template=("x",),
                    full_access_args=(), sandbox_args=())
        base.update(kw)
        return ringer.EngineConfig(**base)

    def test_no_prices_means_unknown_not_zero(self):
        self.assertIsNone(ringer.estimate_cost_from_tokens(self._engine(), 50))

    def test_token_scale_is_applied(self):
        # Codex reports thousands: 50 means 50,000 tokens. At $1/Mtok that is
        # $0.05, not $0.00005 -- the thousand-fold error this guards.
        e = self._engine(token_scale=1000, price_in_per_mtok=1.0)
        self.assertAlmostEqual(ringer.estimate_cost_from_tokens(e, 50), 0.05)

    def test_unscaled_engine_is_priced_per_token(self):
        e = self._engine(token_scale=1, price_in_per_mtok=1.0)
        self.assertAlmostEqual(ringer.estimate_cost_from_tokens(e, 1_000_000), 1.0)

    def test_estimate_never_lands_in_the_measured_field(self):
        rt = ringer.TaskRuntime(task=ringer.TaskSpec(key="k", spec="s", check="c"),
                                taskdir=Path("/tmp"), log_path=Path("/tmp/none.log"))
        self.assertIsNone(rt.cost_usd)
        self.assertIsNone(rt.cost_estimated_usd)


class PortabilityTests(unittest.TestCase):
    """One estate's stack must not be hard-coded into everyone's linter."""

    def _m(self, task_type):
        return ringer.Manifest.from_obj({
            "run_name": "portable", "workdir": tempfile.mkdtemp(), "max_parallel": 1,
            "tasks": [{"key": "t1", "spec": "s", "check": "echo c; test -f out",
                       "verified": "v", "task_type": task_type}]})

    def test_default_covers_only_the_documented_vocabulary(self):
        self.assertEqual(ringer.DEFAULT_TICKETED_TASK_TYPES,
                         frozenset({"code-fix", "code-feature"}))

    def test_an_estate_can_add_its_own_task_types(self):
        # Through the real config path, because that is what another factory
        # would actually do -- a hand-built AppConfig would prove nothing about
        # whether the setting is reachable.
        cfgdir = Path(tempfile.mkdtemp())
        (cfgdir / "config.toml").write_text(
            'ticketed_task_types = ["dotnet-fix"]\n', encoding="utf-8")
        cfg = ringer.AppConfig.load(cfgdir / "config.toml")
        self.assertEqual(cfg.ticketed_task_types, frozenset({"dotnet-fix"}))
        # their type is enforced ...
        self.assertTrue(any("names no ticket" in f
                            for f in ringer.lint_manifest(self._m("dotnet-fix"), config=cfg)))
        # ... and the built-in default is not, because they said what theirs are
        self.assertFalse(any("names no ticket" in f
                             for f in ringer.lint_manifest(self._m("code-fix"), config=cfg)))

    def test_a_bad_setting_is_refused_rather_than_ignored(self):
        cfgdir = Path(tempfile.mkdtemp())
        (cfgdir / "config.toml").write_text(
            'ticketed_task_types = "code-fix"\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            ringer.AppConfig.load(cfgdir / "config.toml")
