#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    CatalogRefreshResult,
    EvalConfig,
    catalog_changes_path,
    normalize_catalog_payload,
    refresh_openrouter_catalog,
    run_catalog_command,
    run_models_command,
    start_catalog_auto_refresh,
)


def source_file(root: Path, name: str, models: list[dict[str, object]]) -> Path:
    path = root / name
    path.write_text(json.dumps({"data": models}), encoding="utf-8")
    return path


def model(
    model_id: str,
    *,
    prompt: str,
    completion: str,
    context_length: int = 64000,
    modality: str = "text->text",
) -> dict[str, object]:
    return {
        "id": model_id,
        "name": model_id,
        "context_length": context_length,
        "architecture": {"modality": modality},
        "pricing": {"prompt": prompt, "completion": completion},
    }


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        # Isolate ALL default state paths: without this, tests that hit
        # default_read_model_db_path() sync fixture rows into the REAL
        # ~/.ringer/ringer.db (this exact leak put 'proven-model' on the
        # live public scoreboard, 2026-07-10).
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_pricing_per_m_and_free_detection(self) -> None:
        models = normalize_catalog_payload(
            {
                "data": [
                    model("paid", prompt="0.0000005", completion="0.0000015"),
                    model("zero", prompt="0", completion="0"),
                    model("promo:free", prompt="0.000002", completion="0.000003"),
                    model("openrouter/auto", prompt="-1", completion="-1"),
                ]
            },
            fetched_at="2026-07-06T00:00:00+00:00",
        )
        by_id = {item["id"]: item for item in models}
        self.assertEqual(0.5, by_id["paid"]["prompt_per_m"])
        self.assertEqual(1.5, by_id["paid"]["completion_per_m"])
        self.assertFalse(by_id["paid"]["free"])
        self.assertTrue(by_id["zero"]["free"])
        self.assertTrue(by_id["promo:free"]["free"])
        self.assertIsNone(by_id["openrouter/auto"]["prompt_per_m"])
        self.assertIsNone(by_id["openrouter/auto"]["completion_per_m"])
        self.assertTrue(by_id["openrouter/auto"]["variable_pricing"])
        self.assertFalse(by_id["openrouter/auto"]["pricing_unknown"])
        self.assertFalse(by_id["openrouter/auto"]["free"])

    def test_refresh_appends_diff_events(self) -> None:
        snapshot = self.root / "catalog.json"
        old_source = source_file(
            self.root,
            "old.json",
            [
                model("changed", prompt="0.000001", completion="0.000002"),
                model("removed", prompt="0.000003", completion="0.000004"),
            ],
        )
        new_source = source_file(
            self.root,
            "new.json",
            [
                model("changed", prompt="0", completion="0"),
                model("added", prompt="0.000005", completion="0.000006"),
            ],
        )

        refresh_openrouter_catalog(snapshot, source=str(old_source))
        refresh_openrouter_catalog(snapshot, source=str(new_source))

        rows = [
            json.loads(line)
            for line in catalog_changes_path(snapshot).read_text(encoding="utf-8").splitlines()
        ]
        kinds_by_id = {(row["kind"], row["id"]) for row in rows}
        self.assertIn(("added", "added"), kinds_by_id)
        self.assertIn(("removed", "removed"), kinds_by_id)
        self.assertIn(("price_change", "changed"), kinds_by_id)
        self.assertIn(("went_free", "changed"), kinds_by_id)

    def test_catalog_free_filter_json_output(self) -> None:
        snapshot = self.root / "catalog.json"
        source = source_file(
            self.root,
            "source.json",
            [
                model("paid", prompt="0.000001", completion="0.000001"),
                model("free", prompt="0", completion="0"),
                model("openrouter/auto", prompt="-1", completion="-1"),
            ],
        )
        refresh_openrouter_catalog(snapshot, source=str(source))
        args = argparse.Namespace(
            refresh=False,
            source=None,
            file=snapshot,
            free=True,
            changes=False,
            json=True,
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = run_catalog_command(args)

        self.assertEqual(0, rc)
        payload = json.loads(out.getvalue())
        self.assertEqual(["free"], [item["id"] for item in payload])

    def test_catalog_table_shows_variable_pricing_after_priced_models(self) -> None:
        snapshot = self.root / "catalog.json"
        source = source_file(
            self.root,
            "source.json",
            [
                model("openrouter/auto", prompt="-1", completion="-1"),
                model("cheap", prompt="0.0000001", completion="0.0000001"),
                model("free", prompt="0", completion="0"),
            ],
        )
        refresh_openrouter_catalog(snapshot, source=str(source))
        args = argparse.Namespace(
            refresh=False,
            source=None,
            file=snapshot,
            free=False,
            changes=False,
            json=False,
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = run_catalog_command(args)

        self.assertEqual(0, rc)
        rows = [line for line in out.getvalue().splitlines() if line and not line.startswith(("id", "-"))]
        self.assertEqual("free", rows[0].split()[0])
        self.assertEqual("cheap", rows[1].split()[0])
        self.assertEqual("openrouter/auto", rows[2].split()[0])
        self.assertIn("      var       var ", rows[2])
        self.assertNotIn("FREE", rows[2])

    def test_models_explore_tiers_and_candidates(self) -> None:
        log_path = self.root / "eval.jsonl"
        rows = [
            {
                "run_id": f"run{i}",
                "task_key": "task",
                "worker_engine": "opencode",
                "model": "tiered-alpha",
                "task_type": "code-feature",
                "verdict": "PASS",
                "logged_at": f"2026-07-0{i}T10:00:00+00:00",
            }
            for i in range(1, 4)
        ]
        rows.append(
            {
                "run_id": "probation-run",
                "task_key": "task",
                "worker_engine": "opencode",
                "model": "tiered-beta",
                "task_type": "code-feature",
                "verdict": "FAIL",
                "logged_at": "2026-07-04T10:00:00+00:00",
            }
        )
        log_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        catalog_path = self.root / "catalog.json"
        source = source_file(
            self.root,
            "catalog-source.json",
            [
                model("tiered-alpha", prompt="0", completion="0"),
                model("tiered-beta", prompt="0.000001", completion="0.000001"),
                model("free-candidate:free", prompt="0.000002", completion="0.000002"),
                model("cheap-candidate", prompt="0.0000005", completion="0.0000005"),
                model("openrouter/auto", prompt="-1", completion="-1"),
                model("image-model", prompt="0", completion="0", modality="text+image->text"),
                model("small-context", prompt="0", completion="0", context_length=16000),
                model("embedder", prompt="0", completion="0", modality="text->embedding"),
            ],
        )
        refresh_openrouter_catalog(catalog_path, source=str(source))
        config = AppConfig(
            path=None,
            identity_default=None,
            state_dir=self.root / "state",
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=log_path),
            engines={},
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(self.root / "live.html"),
                report_template=str(self.root / "report.html"),
                index_out=self.root / "index.html",
            ),
        )
        args = argparse.Namespace(
            log=log_path,
            task_type="code-feature",
            model=None,
            engine=None,
            since=None,
            explore=True,
            catalog_file=catalog_path,
            json=False,
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = run_models_command(config, args)

        output = out.getvalue()
        self.assertEqual(0, rc)
        self.assertIn("proven    ", output)
        self.assertIn("probation ", output)
        self.assertIn("untested  free-candidate:free", output)
        self.assertIn("untested  cheap-candidate", output)
        self.assertNotIn("openrouter/auto", output)
        self.assertNotIn("image-model", output)
        self.assertNotIn("small-context", output)
        self.assertNotIn("embedder", output)

    def test_missing_or_malformed_pricing_is_unknown_variable_not_free(self) -> None:
        snapshot = self.root / "catalog.json"
        source = source_file(
            self.root,
            "unknown-pricing.json",
            [
                {
                    "id": "missing-pricing",
                    "name": "missing-pricing",
                    "context_length": 64000,
                    "architecture": {"modality": "text->text"},
                    "pricing": None,
                },
                {
                    "id": "malformed-pricing",
                    "name": "malformed-pricing",
                    "context_length": 64000,
                    "architecture": {"modality": "text->text"},
                    "pricing": {"prompt": "abc", "completion": "0"},
                },
                model("free", prompt="0", completion="0"),
                model("cheap-candidate", prompt="0.0000005", completion="0.0000005"),
            ],
        )
        refresh_openrouter_catalog(snapshot, source=str(source))

        models = json.loads(snapshot.read_text(encoding="utf-8"))["models"]
        by_id = {item["id"]: item for item in models}
        for model_id in ("missing-pricing", "malformed-pricing"):
            self.assertTrue(by_id[model_id]["pricing_unknown"])
            self.assertTrue(by_id[model_id]["variable_pricing"])
            self.assertIsNone(by_id[model_id]["prompt_per_m"])
            self.assertIsNone(by_id[model_id]["completion_per_m"])
            self.assertFalse(by_id[model_id]["free"])

        free_args = argparse.Namespace(
            refresh=False,
            source=None,
            file=snapshot,
            free=True,
            changes=False,
            json=True,
        )
        free_out = io.StringIO()
        with contextlib.redirect_stdout(free_out):
            self.assertEqual(0, run_catalog_command(free_args))
        self.assertEqual(["free"], [item["id"] for item in json.loads(free_out.getvalue())])

        log_path = self.root / "eval.jsonl"
        log_path.write_text("", encoding="utf-8")
        config = AppConfig(
            path=None,
            identity_default=None,
            state_dir=self.root / "state",
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=log_path),
            engines={},
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(self.root / "live.html"),
                report_template=str(self.root / "report.html"),
                index_out=self.root / "index.html",
            ),
        )
        explore_args = argparse.Namespace(
            log=log_path,
            task_type=None,
            model=None,
            engine=None,
            since=None,
            explore=True,
            catalog_file=snapshot,
            json=False,
        )
        explore_out = io.StringIO()
        with contextlib.redirect_stdout(explore_out):
            self.assertEqual(0, run_models_command(config, explore_args))
        output = explore_out.getvalue()
        self.assertIn("cheap-candidate", output)
        self.assertNotIn("missing-pricing", output)
        self.assertNotIn("malformed-pricing", output)

    def test_variable_pricing_transitions_log_once_and_fixed_prices(self) -> None:
        snapshot = self.root / "catalog.json"
        old_source = source_file(
            self.root,
            "old.json",
            [
                model("to-variable", prompt="0", completion="0"),
                model("to-fixed", prompt="-1", completion="-1"),
                model("to-free", prompt="-1", completion="-1"),
            ],
        )
        sentinel_source = source_file(
            self.root,
            "sentinel.json",
            [
                model("to-variable", prompt="-1", completion="-1"),
                model("to-fixed", prompt="0.000001", completion="0.000002"),
                model("to-free", prompt="0", completion="0"),
            ],
        )

        refresh_openrouter_catalog(snapshot, source=str(old_source))
        refresh_openrouter_catalog(snapshot, source=str(sentinel_source))
        refresh_openrouter_catalog(snapshot, source=str(sentinel_source))

        rows = [
            json.loads(line)
            for line in catalog_changes_path(snapshot).read_text(encoding="utf-8").splitlines()
        ]
        rows_by_id: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            rows_by_id.setdefault(str(row["id"]), []).append(row)
        self.assertEqual(
            ["added", "pricing_variable"],
            [row["kind"] for row in rows_by_id["to-variable"]],
        )
        self.assertEqual(
            ["added", "pricing_fixed"],
            [row["kind"] for row in rows_by_id["to-fixed"]],
        )
        fixed = rows_by_id["to-fixed"][1]
        self.assertEqual(1.0, fixed["new_prompt_per_m"])
        self.assertEqual(2.0, fixed["new_completion_per_m"])
        self.assertEqual(
            ["added", "pricing_fixed", "went_free"],
            [row["kind"] for row in rows_by_id["to-free"]],
        )

    def test_refresh_appends_events_before_snapshot_replace(self) -> None:
        snapshot = self.root / "catalog.json"
        old_source = source_file(
            self.root,
            "old.json",
            [model("changed", prompt="0.000001", completion="0.000002")],
        )
        new_source = source_file(
            self.root,
            "new.json",
            [model("changed", prompt="0", completion="0")],
        )

        refresh_openrouter_catalog(snapshot, source=str(old_source))
        before = snapshot.read_text(encoding="utf-8")

        with mock.patch("ringer.atomic_write_json", side_effect=RuntimeError("write stopped")):
            with self.assertRaisesRegex(RuntimeError, "write stopped"):
                refresh_openrouter_catalog(snapshot, source=str(new_source))

        self.assertEqual(before, snapshot.read_text(encoding="utf-8"))
        rows = [
            json.loads(line)
            for line in catalog_changes_path(snapshot).read_text(encoding="utf-8").splitlines()
        ]
        self.assertIn(("price_change", "changed"), {(row["kind"], row["id"]) for row in rows})
        self.assertIn(("went_free", "changed"), {(row["kind"], row["id"]) for row in rows})

    def test_auto_refresh_throttling_env_and_exception_swallowing(self) -> None:
        snapshot = self.root / "catalog.json"
        snapshot.write_text('{"models":[]}', encoding="utf-8")
        with mock.patch("ringer.refresh_openrouter_catalog") as refresh:
            self.assertIsNone(start_catalog_auto_refresh(snapshot_path=snapshot, print_notice=False))
            refresh.assert_not_called()

        stale = time.time() - (25 * 60 * 60)
        os.utime(snapshot, (stale, stale))
        with mock.patch("ringer.refresh_openrouter_catalog") as refresh:
            refresh.return_value = CatalogRefreshResult(
                path=snapshot,
                changes_path=catalog_changes_path(snapshot),
                models=[],
                events=[],
            )
            thread = start_catalog_auto_refresh(snapshot_path=snapshot, print_notice=False)
            self.assertIsNotNone(thread)
            assert thread is not None
            thread.join(timeout=2)
            refresh.assert_called_once()

        os.environ["RINGER_NO_CATALOG_REFRESH"] = "1"
        with mock.patch("ringer.refresh_openrouter_catalog") as refresh:
            self.assertIsNone(start_catalog_auto_refresh(snapshot_path=snapshot, print_notice=False))
            refresh.assert_not_called()
        os.environ.pop("RINGER_NO_CATALOG_REFRESH")

        with mock.patch("ringer.refresh_openrouter_catalog", side_effect=RuntimeError("boom")):
            thread = start_catalog_auto_refresh(snapshot_path=snapshot, print_notice=False)
            self.assertIsNotNone(thread)
            assert thread is not None
            thread.join(timeout=2)

    def test_auto_refresh_free_notice_goes_to_stderr(self) -> None:
        snapshot = self.root / "catalog.json"
        snapshot.write_text('{"models":[]}', encoding="utf-8")
        stale = time.time() - (25 * 60 * 60)
        os.utime(snapshot, (stale, stale))

        result = CatalogRefreshResult(
            path=snapshot,
            changes_path=catalog_changes_path(snapshot),
            models=[],
            events=[{"kind": "went_free", "id": "new-free-model"}],
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("ringer.refresh_openrouter_catalog", return_value=result):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                thread = start_catalog_auto_refresh(snapshot_path=snapshot, print_notice=True)
                self.assertIsNotNone(thread)
                assert thread is not None
                thread.join(timeout=2)

        self.assertEqual("", stdout.getvalue())
        self.assertIn("Catalog refresh: model went FREE: new-free-model", stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
