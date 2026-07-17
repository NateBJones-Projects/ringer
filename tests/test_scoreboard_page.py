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
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    AppConfig,
    ArtifactConfig,
    EvalConfig,
    Manifest,
    TaskSpec,
    artifact_library_path,
    artifact_live_path,
    lint_manifest,
    model_judgment_notes,
    parse_model_notes_sections,
    run_models_command,
)


def catalog_model(model_id: str, *, prompt: str, completion: str, ctx: int = 64000) -> dict[str, object]:
    return {
        "id": model_id,
        "name": model_id,
        "context_length": ctx,
        "architecture": {"modality": "text->text"},
        "pricing": {"prompt": prompt, "completion": completion},
    }


def attempt(
    *,
    run_id: str,
    task_key: str,
    model: str,
    task_type: str,
    verdict: str,
    retry: bool,
    logged_at: str,
    tokens: int | None = 1000,
    engine: str = "opencode",
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "task_key": task_key,
        "worker_engine": engine,
        "model": model,
        "task_type": task_type,
        "verdict": verdict,
        "retry": retry,
        "worker_tokens": tokens,
        "duration_ms": 100,
        "logged_at": logged_at,
    }


class ScoreboardPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.log_path = self.root / "eval.jsonl"
        self.catalog_path = self.root / "catalog.json"
        self.notes_path = self.root / "MODEL-NOTES.md"
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
                enabled=True,
                out_template=str(self.root / "live.html"),
                report_template=str(self.root / "report.html"),
                index_out=self.root / "index.html",
            ),
        )
        self.write_fixtures()

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def write_fixtures(self) -> None:
        rows: list[dict[str, object]] = []
        for index in range(20):
            run_id = f"proven-{index:02d}"
            task_key = "task"
            if index < 18:
                rows.append(
                    attempt(
                        run_id=run_id,
                        task_key=task_key,
                        model="openrouter/proven",
                        task_type="code-feature",
                        verdict="PASS",
                        retry=False,
                        logged_at=f"2026-07-06T10:{index:02d}:00+00:00",
                        tokens=2000,
                    )
                )
            else:
                rows.append(
                    attempt(
                        run_id=run_id,
                        task_key=task_key,
                        model="openrouter/proven",
                        task_type="code-feature",
                        verdict="FAIL",
                        retry=False,
                        logged_at=f"2026-07-06T10:{index:02d}:00+00:00",
                        tokens=2000,
                    )
                )
                rows.append(
                    attempt(
                        run_id=run_id,
                        task_key=task_key,
                        model="openrouter/proven",
                        task_type="code-feature",
                        verdict="PASS",
                        retry=True,
                        logged_at=f"2026-07-06T11:{index:02d}:00+00:00",
                        tokens=2200,
                    )
                )
        rows.append(
            attempt(
                run_id="probation-1",
                task_key="task",
                model="openrouter/probation",
                task_type="code-feature",
                verdict="PASS",
                retry=False,
                logged_at="2026-07-06T12:00:00+00:00",
                tokens=500,
            )
        )
        rows.append(
            attempt(
                run_id="free-1",
                task_key="task",
                model="openrouter/free:free",
                task_type="research",
                verdict="PASS",
                retry=False,
                logged_at="2026-07-06T12:10:00+00:00",
                tokens=700,
            )
        )
        rows.append(
            attempt(
                run_id="codex-1",
                task_key="task",
                model="",
                task_type="site-build",
                verdict="PASS",
                retry=False,
                logged_at="2026-07-06T12:20:00+00:00",
                tokens=None,
                engine="codex",
            )
        )
        self.log_path.write_text("\n".join(json.dumps(row) for row in rows) + "\nnot-json\n", encoding="utf-8")
        self.catalog_path.write_text(
            json.dumps(
                {
                    "models": [
                        catalog_model("openrouter/proven", prompt="0.000001", completion="0.000003"),
                        catalog_model("openrouter/probation", prompt="0.0000005", completion="0.000001"),
                        catalog_model("openrouter/free:free", prompt="0.000002", completion="0.000004", ctx=128000),
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.catalog_path.with_name("catalog.changes.jsonl").write_text(
            json.dumps({"ts": "2026-07-06T12:00:00+00:00", "kind": "went_free", "id": "openrouter/free:free"})
            + "\n",
            encoding="utf-8",
        )
        self.notes_path.write_text(
            """# Notes

## Proven lane (`openrouter/proven`)

- Non-dated setup line ignored by the parser.
- 2026-07-06 — steady `code-feature` performance across a larger sample.
  Continuation line kept with the dated bullet.
- 2026-07-05 - second newest note.
- 2026-07-04 - third newest note.
- 2026-07-03 - fourth newest note.
- 2026-07-02 - fifth newest note.
- 2026-07-01 - older note should stay behind the cap.

## codex

- 2026-07-06 - flat-plan site work; no token billing in the log.
""",
            encoding="utf-8",
        )

    def args(self, *, html: str | None = None, open: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            log=self.log_path,
            task_type=None,
            model=None,
            engine=None,
            since=None,
            explore=False,
            catalog_file=self.catalog_path,
            notes_file=self.notes_path,
            html=html,
            open=open,
            json=False,
        )

    def render_to(self, path: Path) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, run_models_command(self.config, self.args(html=str(path))))
        self.assertEqual(str(path.resolve()) + "\n", out.getvalue())
        return path.read_text(encoding="utf-8")

    def test_html_render_contains_ranked_table_badges_bars_costs_and_normalized_notes(self) -> None:
        html = self.render_to(self.root / "scoreboard.html")

        expected_headers = [
            "Model",
            "Lab",
            "Harness",
            "API/Plan",
            "Tier",
            "Tasks",
            "First try",
            "Pass",
            "Tokens (median)",
            "Speed (median)",
            "Last used",
            "Notes",
        ]
        last_index = -1
        for header in expected_headers:
            index = html.index(f">{header}<")
            self.assertGreater(index, last_index)
            last_index = index

        self.assertIn('<table class="ranked-table">', html)
        self.assertIn('<div class="table-scroll">', html)
        self.assertNotIn("model-card", html)
        self.assertNotIn("source-grid", html)
        table_body = html[html.index("<main>") : html.index("</main>")]
        self.assertNotIn("openrouter/proven", table_body)
        self.assertNotIn("openrouter/probation", table_body)
        self.assertNotIn("openrouter/free:free", table_body)
        self.assertIn('<div class="model-name">Proven</div>', table_body)
        self.assertIn('<div class="model-name">Probation</div>', table_body)
        self.assertIn('<div class="model-name">Free</div>', table_body)
        # Blank-model codex rows are quarantined, never rendered as GPT-5.5
        # (taxonomy contract, docs/TAXONOMY.md).
        self.assertNotIn("GPT-5.5", html)
        self.assertIn('<div class="model-name">(unattributed legacy rows)</div>', html)
        self.assertIn("quarantined legacy data", html)
        self.assertIn('<span class="tier-badge proven">proven</span>', html)
        self.assertIn('<span class="tier-badge probation">probation</span>', html)
        self.assertIn("n=20", html)
        self.assertIn('class="rate-bar bar-fill" style="width: 90%"', html)
        self.assertIn('class="rate-bar bar-fill" style="width: 100%"', html)
        self.assertIn('<td class="num">2,000</td>', html)
        self.assertIn(">0s</td>", html)
        self.assertNotIn("Est. $/task", html)
        self.assertIn("<time>July 6</time><span>steady code-feature performance across a larger sample. Continuation line kept with the dated bullet.</span>", html)
        self.assertNotIn("`code-feature`", html)
        self.assertIn("fifth newest note", html)
        self.assertIn("older note should stay behind the cap", html)
        self.assertIn("more in model notes", html)
        self.assertIn("no judgment notes yet", html)

    def test_html_header_links_watchlist_and_footer_diagnostics(self) -> None:
        generated_at = ringer.datetime.fromisoformat("2026-07-06T12:20:00+00:00")
        with mock.patch.object(ringer, "datetime", wraps=ringer.datetime) as mocked_datetime:
            mocked_datetime.now.return_value = generated_at
            html = self.render_to(self.root / "scoreboard.html")

        self.assertIn("<h1 class=\"scoreboard-title\">Model performance scoreboard</h1>", html)
        self.assertIn("<span>Generated July 6, 2026</span>", html)
        self.assertIn('>eval log</a>', html)
        self.assertIn('>catalog</a>', html)
        self.assertIn('>model notes</a>', html)
        self.assertIn(f'title="{self.log_path.resolve()}"', html)
        self.assertIn(f'title="{self.catalog_path.resolve()}"', html)
        self.assertIn(f'title="{self.notes_path.resolve()}"', html)
        header_html = html[: html.index('<section class="watchlist"')]
        self.assertNotIn("rows read", header_html)
        self.assertNotIn("skipped", header_html)

        self.assertEqual(1, html.count('class="watchlist"'))
        self.assertIn("1 models free on OpenRouter right now", html)
        self.assertNotIn('data-model="openrouter/free:free"', html)
        self.assertNotIn('title="openrouter/free:free"', html)
        self.assertIn("Free · 128K ctx", html)
        self.assertIn("Free went free — July 6", html)
        self.assertNotIn("went_free", html)

        footer_start = html.index('<footer class="scoreboard-footer">')
        footer_html = html[footer_start:]
        self.assertIn("25 rows read, 1 skipped lines.", footer_html)
        self.assertIn("Ordering sorts by evidence tier first", footer_html)
        self.assertIn("Misrouted and unattributed legacy rows are not ranked or tiered", footer_html)

    def test_evidence_floor_orders_probation_after_proven_model(self) -> None:
        html = self.render_to(self.root / "scoreboard.html")

        self.assertLess(html.index(">Proven<"), html.index(">Probation<"))
        proven_row = html[html.index(">Proven<") :]
        proven_row = proven_row[: proven_row.index("</tr>")]
        self.assertIn('<span class="tier-badge proven">proven</span>', proven_row)

    def test_reused_task_identity_does_not_collapse_proven_evidence(self) -> None:
        rows: list[dict[str, object]] = []
        for index in range(20):
            rows.append(
                attempt(
                    run_id="shared-run",
                    task_key="shared-task",
                    model="openrouter/proven",
                    task_type="code-feature",
                    verdict="PASS" if index < 18 else "FAIL",
                    retry=False,
                    logged_at=f"2026-07-06T13:{index:02d}:00+00:00",
                    tokens=2000,
                )
            )
        rows.append(
            attempt(
                run_id="shared-run",
                task_key="shared-task",
                model="openrouter/probation",
                task_type="code-feature",
                verdict="PASS",
                retry=False,
                logged_at="2026-07-06T14:00:00+00:00",
                tokens=500,
            )
        )
        self.log_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

        html = self.render_to(self.root / "scoreboard.html")

        self.assertLess(html.index(">Proven<"), html.index(">Probation<"))
        self.assertIn("n=20", html)
        row_start = html.rfind('<tr class="model-row"', 0, html.index(">Proven<"))
        row_end = html.index("</tr>", row_start)
        self.assertIn('<span class="tier-badge proven">proven</span>', html[row_start:row_end])

    def test_html_path_does_not_write_artifact_library(self) -> None:
        html_path = self.root / "custom.html"
        self.render_to(html_path)

        self.assertTrue(html_path.exists())
        self.assertFalse(artifact_library_path(self.config.state_dir).exists())

    def test_html_path_with_open_writes_and_opens_explicit_path_without_library(self) -> None:
        html_path = self.root / "custom-open.html"
        out = io.StringIO()
        with mock.patch.object(ringer, "open_in_browser") as open_in_browser:
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, run_models_command(self.config, self.args(html=str(html_path), open=True)))

        self.assertEqual(str(html_path.resolve()) + "\n", out.getvalue())
        self.assertTrue(html_path.exists())
        self.assertFalse(artifact_library_path(self.config.state_dir).exists())
        open_in_browser.assert_called_once_with(ringer.file_href(html_path.resolve()))

    def test_html_without_path_writes_live_artifact_library_entry(self) -> None:
        out = io.StringIO()
        args = self.args(html="")
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, run_models_command(self.config, args))

        live_path = artifact_live_path(self.config.state_dir, "model-scoreboard")
        self.assertEqual(str(live_path.resolve()) + "\n", out.getvalue())
        self.assertTrue(live_path.exists())
        library = json.loads(artifact_library_path(self.config.state_dir).read_text(encoding="utf-8"))
        self.assertIn("model-scoreboard", library["artifacts"])

    def test_html_is_self_contained_with_no_external_resource_loads(self) -> None:
        html = self.render_to(self.root / "scoreboard.html")

        self.assertNotRegex(html, r"""(?:src|href)=["']https?://""")
        self.assertNotRegex(html, r"""@import\s+["']https?://""")
        self.assertNotIn("<script", html.lower())
        self.assertIn("@media (prefers-color-scheme: light)", html)
        self.assertIn("@media (prefers-color-scheme: dark)", html)

    def test_notes_section_parser_matches_heading_and_missing_section_falls_back(self) -> None:
        sections = parse_model_notes_sections(self.notes_path)

        proven = model_judgment_notes("openrouter/proven", sections)
        self.assertEqual(6, len(proven))
        self.assertIn("steady `code-feature` performance", proven[0])
        self.assertIn("Continuation line kept", proven[0])
        self.assertNotIn("Non-dated setup", "\n".join(proven))
        self.assertEqual([], model_judgment_notes("openrouter/missing", sections))

    def test_notes_matching_uses_id_boundaries_and_best_heading(self) -> None:
        notes_path = self.root / "MODEL-NOTES-boundaries.md"
        notes_path.write_text(
            """# Notes

## gpt-4o

- 2026-07-06 - four-o note.

## runner lane (`gpt-4`)

- 2026-07-06 - generic four note.

## gpt-4

- 2026-07-06 - exact four note.
""",
            encoding="utf-8",
        )
        sections = parse_model_notes_sections(notes_path)

        gpt4_notes = model_judgment_notes("gpt-4", sections)
        gpt4o_notes = model_judgment_notes("gpt-4o", sections)

        self.assertEqual(1, len(gpt4_notes))
        self.assertIn("exact four note", gpt4_notes[0])
        self.assertNotIn("four-o note", gpt4_notes[0])
        self.assertEqual(1, len(gpt4o_notes))
        self.assertIn("four-o note", gpt4o_notes[0])
        self.assertNotIn("exact four note", gpt4o_notes[0])

    def test_notes_parser_returns_empty_for_directory_path(self) -> None:
        self.assertEqual({}, parse_model_notes_sections(self.root))

    def test_manifest_rejects_reserved_scoreboard_run_name_and_lints_it(self) -> None:
        task_obj = {
            "key": "one",
            "spec": "Create the requested artifact, keep the work scoped, and write a clear verification output.",
            "check": "test -s output.txt",
            "expect_files": ["output.txt"],
            "verified": "output.txt exists",
        }
        with self.assertRaisesRegex(ValueError, "run_name model-scoreboard is reserved for the scoreboard page"):
            Manifest.from_obj(
                {
                    "run_name": "model-scoreboard",
                    "workdir": str(self.root / "work"),
                    "max_parallel": 1,
                    "tasks": [task_obj],
                }
            )

        manifest = Manifest(
            run_name="model-scoreboard",
            workdir=self.root / "work",
            max_parallel=1,
            worktrees=False,
            repo=None,
            tasks=(TaskSpec.from_obj(task_obj),),
        )
        self.assertIn(
            "manifest: run_name model-scoreboard is reserved for the scoreboard page.",
            lint_manifest(manifest),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
