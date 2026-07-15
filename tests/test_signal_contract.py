#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EvalConfig,
    Manifest,
    MODEL_SCOREBOARD_COLUMNS,
    build_models_api_payload,
    inject_models_tab_into_ringside_html,
    lint_manifest,
    load_model_identity_registry,
    run_models_command,
)


def attempt(
    run_id: str,
    model: str,
    *,
    engine: str = "opencode",
    task_type: str = "code-feature",
    logged_at: str = "2026-07-15T12:00:00+00:00",
    duration_ms: int = 272_000,
    tokens: int = 1_234,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "task_key": "task",
        "worker_engine": engine,
        "model": model,
        "task_type": task_type,
        "verdict": "PASS",
        "retry": False,
        "duration_ms": duration_ms,
        "worker_tokens": tokens,
        "logged_at": logged_at,
    }


class SignalContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.log_path = self.root / "runs.jsonl"
        self.registry_path = self.root / "model-identity.toml"
        self.notes_path = self.root / "MODEL-NOTES.md"
        self.catalog_path = self.root / "catalog.json"
        self.html_path = self.root / "scoreboard.html"
        self.registry_path.write_text(
            """
[engines.grok]
harness = "Grok Build CLI"
access = "OAuth plan"
default_model_key = "grok-build"

[engines.grok.models."grok-build"]
display = "Grok 4.5"
lab = "xAI"
confidence = "verified"
source = "fixture"
noncanonical_slugs = ["opencode:openrouter/x-ai/grok-4.5"]

[engines.opencode]
harness = "OpenCode"
access = "OpenRouter API"

[engines.opencode.models."openrouter/acme/known-model"]
display = "Known Model"
lab = "Acme Lab"
confidence = "verified"
source = "fixture"
""",
            encoding="utf-8",
        )
        rows = [
            attempt("known", "openrouter/acme/known-model"),
            attempt("unknown", "openrouter/vendor/secret-model", task_type="research"),
            attempt("misroute-1", "openrouter/x-ai/grok-4.5"),
            attempt("misroute-2", "openrouter/x-ai/grok-4.5"),
            attempt("misroute-3", "openrouter/x-ai/grok-4.5"),
        ]
        self.log_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        self.notes_path.write_text(
            """# Notes

## Known Model

- 2026-07-14 — older known-model note.
- 2026-07-15 — newest known-model note with the actual result.

## grok-build

- 2026-07-15 — canonical-key note for the misrouted artifact.
""",
            encoding="utf-8",
        )
        self.catalog_path.write_text('{"models": []}', encoding="utf-8")
        self.config = AppConfig(
            path=None,
            identity_default=None,
            state_dir=self.root / "state",
            dashboard_port_base=8787,
            hud_port=8700,
            hud_app_path=None,
            allow_full_access=False,
            eval=EvalConfig(backend="jsonl", jsonl_path=self.log_path),
            engines={},
            artifact=ArtifactConfig(
                enabled=False,
                out_template=str(self.root / "live.html"),
                report_template=str(self.root / "report.html"),
                index_out=self.root / "index.html",
            ),
        )

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def args(self, *, html: str | None = None) -> argparse.Namespace:
        return argparse.Namespace(
            log=self.log_path,
            db=None,
            task_type=None,
            model=None,
            engine=None,
            since=None,
            explore=False,
            catalog_file=self.catalog_path,
            notes_file=self.notes_path,
            registry=self.registry_path,
            html=html,
            open=False,
            json=False,
        )

    def payload(self) -> dict[str, object]:
        return build_models_api_payload(
            log_path=self.log_path,
            default_log_path=self.root / "other.jsonl",
            catalog_path=self.catalog_path,
            registry_path=self.registry_path,
            notes_path=self.notes_path,
        )

    def test_all_three_surfaces_emit_contract_columns_in_order(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, run_models_command(self.config, self.args()))
        cli = output.getvalue()
        cli_header = next(
            line for line in cli.splitlines() if line.startswith("Model ") and " | " in line
        )
        self.assertEqual(list(MODEL_SCOREBOARD_COLUMNS), [part.strip() for part in cli_header.split("|")])

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                0, run_models_command(self.config, self.args(html=str(self.html_path)))
            )
        html = self.html_path.read_text(encoding="utf-8")
        header = html[html.index("<thead>") : html.index("</thead>")]
        positions = [header.index(f">{column}<") for column in MODEL_SCOREBOARD_COLUMNS]
        self.assertEqual(sorted(positions), positions)

        ringside = ringer.read_ringside_html()
        ringside_header = ringside[ringside.index("'<th>Model") :]
        ringside_header = ringside_header[: ringside_header.index("</tr></thead>")]
        positions = [ringside_header.index(f">{column}<") for column in MODEL_SCOREBOARD_COLUMNS]
        self.assertEqual(sorted(positions), positions)
        self.assertEqual(list(MODEL_SCOREBOARD_COLUMNS), self.payload()["columns"])

    def test_chart_rows_hide_slugs_and_notes_are_fresh(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, run_models_command(self.config, self.args()))
        cli = output.getvalue()
        cli_rows = cli[: cli.index("Judgment layer:")]
        self.assertNotIn("openrouter/acme/known-model", cli_rows)
        self.assertNotIn("openrouter/vendor/secret-model", cli_rows)
        self.assertIn("Known Model", cli_rows)
        self.assertIn("Secret Model [unregistered]", cli_rows)
        self.assertIn("newest known-model note", cli_rows)

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                0, run_models_command(self.config, self.args(html=str(self.html_path)))
            )
        html = self.html_path.read_text(encoding="utf-8")
        body = html[html.index("<main>") : html.index("</main>")]
        self.assertNotIn("openrouter/acme/known-model", body)
        self.assertNotIn("openrouter/vendor/secret-model", body)
        self.assertIn("newest known-model note with the actual result", body)
        secret_row = body[body.index(">Secret Model</div>") :]
        secret_row = secret_row[: secret_row.index("</tr>")]
        self.assertIn('class="notes-cell" title=""></td>', secret_row)

    def test_noncanonical_lint_escape_and_misrouted_credit(self) -> None:
        manifest = Manifest.from_obj(
            {
                "run_name": "route-test",
                "workdir": str(self.root / "work"),
                "tasks": [
                    {
                        "key": "grok-task",
                        "engine": "opencode",
                        "model": "openrouter/x-ai/grok-4.5",
                        "spec": "Create the requested output with enough detail to satisfy the route fixture contract.",
                        "check": "test -s output.txt || { echo missing; exit 1; }",
                        "expect_files": ["output.txt"],
                        "verified": "output.txt exists",
                    }
                ],
            }
        )
        registry = load_model_identity_registry(self.registry_path)
        findings = lint_manifest(manifest, identity_registry=registry)
        self.assertTrue(any("ERROR: grok-task" in item for item in findings))
        self.assertTrue(any("grok:grok-build via Grok Build CLI on OAuth plan" in item for item in findings))
        self.assertEqual(
            [],
            lint_manifest(
                manifest,
                identity_registry=registry,
                allow_noncanonical_route=True,
            ),
        )

        payload = self.payload()
        misrouted = next(row for row in payload["rollup"] if row["misrouted"])
        self.assertEqual(
            ("Grok 4.5", "xAI", "OpenCode", "OpenRouter API"),
            (
                misrouted["model_display"],
                misrouted["lab"],
                misrouted["harness"],
                misrouted["access"],
            ),
        )
        self.assertEqual("unranked", misrouted["tier"])
        self.assertIn("canonical-key note", misrouted["latest_note"])

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, run_models_command(self.config, self.args()))
        cli = output.getvalue()
        self.assertIn("Grok 4.5 [misrouted]", cli)
        self.assertNotIn("openrouter/x-ai/grok-4.5", cli)

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                0, run_models_command(self.config, self.args(html=str(self.html_path)))
            )
        html = self.html_path.read_text(encoding="utf-8")
        self.assertIn(">misrouted</span>", html)
        chart = html[html.index("<main>") : html.index("</main>")]
        self.assertNotIn("openrouter/x-ai/grok-4.5", chart)

    def test_lint_command_reports_error_and_escape_flag_passes(self) -> None:
        manifest_path = self.root / "ringer.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "run_name": "route-cli",
                    "workdir": str(self.root / "work"),
                    "tasks": [
                        {
                            "key": "grok-task",
                            "engine": "opencode",
                            "model": "openrouter/x-ai/grok-4.5",
                            "spec": "Create the requested output with enough detail to satisfy the route fixture contract.",
                            "check": "test -s output.txt || { echo missing; exit 1; }",
                            "expect_files": ["output.txt"],
                            "verified": "output.txt exists",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        real_registry = ROOT / "registry" / "model-identity.toml"
        with mock.patch.object(ringer, "default_model_registry_path", return_value=real_registry), mock.patch.object(
            ringer, "maybe_self_update"
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(1, ringer.main(["lint", str(manifest_path)]))
            self.assertIn("lint: ERROR: grok-task", output.getvalue())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    1,
                    ringer.main(
                        ["run", str(manifest_path), "--dry-run", "--no-dashboard"]
                    ),
                )
            self.assertIn("lint: ERROR: grok-task", output.getvalue())
            self.assertNotIn("Tasks:", output.getvalue())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    0,
                    ringer.main(
                        ["lint", str(manifest_path), "--allow-noncanonical-route"]
                    ),
                )
            self.assertIn("lint: clean", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
