#!/usr/bin/env python3
"""A run total only counts tokens a worker actually reported.

Regression: snapshot() and build_summary() summed `tokens or 0`, so a run
where no engine reported a count wrote `"tokens": 0` to the state file and
/api/runs, and the per-run dashboard turned any null back into "0 tok".
Zero is a measurement; nobody took one.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import EngineConfig, StateWriter, TaskRuntime, TaskSpec


class RunTokenTotalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.state_dir = Path(self._temp.name)

    def _runtime(self, key: str, tokens: int | None) -> TaskRuntime:
        taskdir = self.state_dir / key
        taskdir.mkdir(parents=True, exist_ok=True)
        log_path = taskdir / "worker.log"
        log_path.write_text("worker output\n", encoding="utf-8")
        runtime = TaskRuntime(
            task=TaskSpec(
                key=key,
                spec="Do the thing described here in full.",
                check="true",
                engine="mock",
            ),
            taskdir=taskdir,
            log_path=log_path,
            status="pass",
            attempts=1,
            spec_short="do the thing",
        )
        runtime.tokens = tokens
        return runtime

    def _writer(self, *tokens: int | None) -> StateWriter:
        return StateWriter(
            "run-tokens",
            "Token Run",
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
            [self._runtime(f"task-{index}", value) for index, value in enumerate(tokens)],
            threading.RLock(),
        )

    def totals(self, *tokens: int | None) -> tuple[object, object, object]:
        writer = self._writer(*tokens)
        state = writer.snapshot()
        return state["tokens"], state["totals"]["tokens"], writer.build_summary()["tokens"]

    def test_no_reported_count_is_null_not_zero(self) -> None:
        self.assertEqual((None, None, None), self.totals(None, None))

    def test_partial_reports_sum_only_what_was_reported(self) -> None:
        self.assertEqual((1200, 1200, 1200), self.totals(1200, None))

    def test_a_reported_zero_stays_zero(self) -> None:
        self.assertEqual((0, 0, 0), self.totals(0, None))


def dashboard_functions() -> str:
    """The top-level function declarations of the per-run dashboard script."""
    html = (ROOT / "dashboard" / "dashboard.html").read_text(encoding="utf-8")
    lines = html[html.index("<script>") + len("<script>") : html.index("</script>")].splitlines()
    functions: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not re.match(r"^    (async )?function \w+\(", line):
            index += 1
            continue
        if line.rstrip().endswith("}") and line.count("{") == line.count("}"):
            functions.append(line)
            index += 1
            continue
        end = lines.index("    }", index)
        functions.append("\n".join(lines[index : end + 1]))
        index = end + 1
    return "\n".join(functions)


DASHBOARD_TOKEN_CHECKS = r"""
const assert = require("node:assert/strict");
let openTaskRef = null;
const run = normalizePayload({runs: [{run_id: "r", tokens: null, totals: {tokens: null}}]})[0];
assert.equal(formatTokens(run.tokens), "—", "run header");
const element = () => ({textContent: "", title: ""});
const record = {node: {className: ""}, elements: {
  name: element(), status: element(), activity: element(),
  elapsed: element(), tokens: element(), children: element(),
}};
updateTaskRecord(record, {key: "t", status: "pass", tokens: null}, run, {key: "r"});
assert.equal(record.elements.tokens.textContent, "—", "task row");
updateTaskRecord(record, {key: "t", status: "pass", tokens: 0}, run, {key: "r"});
assert.equal(record.elements.tokens.textContent, "0 tok", "a reported zero");
const counted = normalizePayload({runs: [{run_id: "c", tokens: 1200}]})[0];
assert.equal(formatTokens(counted.tokens), (1200).toLocaleString() + " tok", "a reported count");
console.log("PASS: dashboard tokens");
"""


class DashboardTokenTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is optional; run the browser recipe in TESTING.md")
    def test_unrecorded_tokens_render_as_unrecorded(self) -> None:
        result = subprocess.run(
            ["node", "-e", dashboard_functions() + DASHBOARD_TOKEN_CHECKS],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PASS:", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
