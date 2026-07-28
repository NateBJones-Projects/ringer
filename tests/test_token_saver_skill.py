#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "token-saver"
CODEX_SKILL = ROOT / ".agents" / "skills" / "token-saver"
SELECT = SKILL / "scripts" / "select_context.py"
STATE = SKILL / "scripts" / "state_delta.py"
INSTALLER = ROOT / "scripts" / "install_token_saver.py"


class TokenSaverSkillTests(unittest.TestCase):
    def run_script(
        self,
        script: Path,
        *arguments: str,
        cwd: Path = ROOT,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *arguments],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_skill_names_the_real_limit_and_required_strategies(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        required = (
            "model call that loaded this skill has already begun",
            "Try local code before another model",
            "Read only the passages needed",
            "accepted result, not the whole conversation",
            "Load tools only when the job needs them",
            "smaller or cheaper model",
            "Allow one bounded repair",
            "Never retry a token-limit",
            "Count every model call",
            "combined total falls",
            "Ringer gateway",
            "Ringer is optional",
            "Do not ask the human to run these scripts",
            "save the current result automatically",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_context_selector_keeps_relevant_passage_and_omits_most_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            source = root / "transcript.txt"
            output = root / "packet.txt"
            report = root / "report.json"
            source.write_text(
                ("Unrelated discussion about office logistics.\n" * 1_500)
                + "Wednesday decision: show people how to keep working after a plan limit.\n"
                + ("Unrelated closing discussion.\n" * 1_500),
                encoding="utf-8",
            )

            result = self.run_script(
                SELECT,
                "--request",
                "What was the Wednesday decision about plan limits?",
                "--source",
                str(source),
                "--max-packet-bytes",
                "4000",
                "--output",
                str(output),
                "--report",
                str(report),
            )

            self.assertEqual(0, result.returncode, result.stderr)
            packet = output.read_text(encoding="utf-8")
            selection = json.loads(report.read_text(encoding="utf-8"))
            self.assertIn("keep working after a plan limit", packet)
            self.assertLess(len(packet.encode("utf-8")), source.stat().st_size // 10)
            self.assertGreater(selection["omitted_source_bytes"], 0)

    def test_context_selector_can_find_sources_without_human_file_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            (root / "notes.md").write_text(
                ("Unrelated material.\n" * 500)
                + "The approved launch day is Wednesday.\n"
                + ("More unrelated material.\n" * 500),
                encoding="utf-8",
            )
            report = root / "report.json"

            result = self.run_script(
                SELECT,
                "--request",
                "What is the approved launch day?",
                "--root",
                str(root),
                "--max-packet-bytes",
                "3000",
                "--report",
                str(report),
                cwd=root,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("approved launch day is Wednesday", result.stdout)
            self.assertTrue(
                json.loads(report.read_text(encoding="utf-8"))[
                    "automatic_source_search"
                ]
            )

    def test_accepted_state_packet_excludes_old_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            state = root / "state.json"
            accepted = root / "accepted.md"
            change = root / "change.txt"
            packet = root / "packet.txt"
            accepted.write_text("The accepted answer has three concrete steps.\n", encoding="utf-8")
            change.write_text("Make the second step more specific.\n", encoding="utf-8")
            old_conversation = ("Rejected draft and old tool output.\n" * 2_000).encode("utf-8")

            saved = self.run_script(
                STATE,
                "save",
                "--state",
                str(state),
                "--accepted-file",
                str(accepted),
            )
            self.assertEqual(0, saved.returncode, saved.stderr)
            built = self.run_script(
                STATE,
                "packet",
                "--state",
                str(state),
                "--change-file",
                str(change),
                "--output",
                str(packet),
            )

            self.assertEqual(0, built.returncode, built.stderr)
            text = packet.read_text(encoding="utf-8")
            self.assertIn("accepted answer has three concrete steps", text)
            self.assertIn("Make the second step more specific", text)
            self.assertNotIn("Rejected draft", text)
            self.assertLess(len(text.encode("utf-8")), len(old_conversation) // 100)

    def test_saving_a_new_result_replaces_the_old_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            state = root / "state.json"
            first = self.run_script(
                STATE,
                "save",
                "--state",
                str(state),
                "--accepted",
                "Old accepted answer.",
            )
            self.assertEqual(0, first.returncode, first.stderr)
            second = self.run_script(
                STATE,
                "save",
                "--state",
                str(state),
                "--accepted",
                "New accepted answer.",
            )
            self.assertEqual(0, second.returncode, second.stderr)
            packet = self.run_script(
                STATE,
                "packet",
                "--state",
                str(state),
                "--change",
                "Shorten it.",
            )
            self.assertEqual(0, packet.returncode, packet.stderr)
            self.assertIn("New accepted answer", packet.stdout)
            self.assertNotIn("Old accepted answer", packet.stdout)

    def test_save_creates_working_directory_without_human_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            state = Path(temp_root) / ".token-saver" / "launch-brief.json"
            saved = self.run_script(
                STATE,
                "save",
                "--state",
                str(state),
                "--accepted",
                "Current accepted launch brief.",
            )
            self.assertEqual(0, saved.returncode, saved.stderr)
            self.assertTrue(state.is_file())

    def test_state_packet_stops_before_exceeding_its_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            state = Path(temp_root) / "state.json"
            saved = self.run_script(
                STATE,
                "save",
                "--state",
                str(state),
                "--accepted",
                "x" * 2_000,
            )
            self.assertEqual(0, saved.returncode, saved.stderr)
            packet = self.run_script(
                STATE,
                "packet",
                "--state",
                str(state),
                "--change",
                "Make it shorter.",
                "--max-packet-bytes",
                "1024",
            )
            self.assertEqual(2, packet.returncode)
            self.assertIn("above the 1,024-byte limit", packet.stderr)

    def test_codex_and_claude_skill_packages_are_identical(self) -> None:
        relative_files = (
            Path("SKILL.md"),
            Path("agents/openai.yaml"),
            Path("scripts/context_packet.py"),
            Path("scripts/select_context.py"),
            Path("scripts/state_delta.py"),
        )
        for relative in relative_files:
            with self.subTest(relative=str(relative)):
                self.assertEqual(
                    (SKILL / relative).read_bytes(),
                    (CODEX_SKILL / relative).read_bytes(),
                )

    def test_bundled_selector_code_matches_the_tested_ringer_selector(self) -> None:
        expected = (ROOT / "context_packet.py").read_bytes()
        self.assertEqual(
            expected,
            (SKILL / "scripts" / "context_packet.py").read_bytes(),
        )
        self.assertEqual(
            expected,
            (CODEX_SKILL / "scripts" / "context_packet.py").read_bytes(),
        )

    def test_skill_commands_use_the_loaded_package_not_a_host_specific_path(self) -> None:
        for skill in (SKILL, CODEX_SKILL):
            with self.subTest(skill=str(skill)):
                text = (skill / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(
                    "/absolute/path/to/token-saver/scripts/select_context.py",
                    text,
                )
                self.assertIn("active skill location", text)
                self.assertNotIn(".claude/skills/token-saver/scripts", text)
                self.assertNotIn(".agents/skills/token-saver/scripts", text)

    def test_installer_installs_both_hosts_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            home = Path(temp_root) / "home"
            first = self.run_script(INSTALLER, "--home", str(home))
            self.assertEqual(0, first.returncode, first.stderr)

            codex = home / ".agents" / "skills" / "token-saver"
            claude = home / ".claude" / "skills" / "token-saver"
            for relative in (
                Path("SKILL.md"),
                Path("agents/openai.yaml"),
                Path("scripts/context_packet.py"),
                Path("scripts/select_context.py"),
                Path("scripts/state_delta.py"),
            ):
                with self.subTest(relative=str(relative)):
                    self.assertEqual(
                        (codex / relative).read_bytes(),
                        (claude / relative).read_bytes(),
                    )
            before = (codex / "SKILL.md").stat().st_mtime_ns
            second = self.run_script(INSTALLER, "--home", str(home))
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertIn("Codex: already current", second.stdout)
            self.assertIn("Claude Code: already current", second.stdout)
            self.assertEqual(before, (codex / "SKILL.md").stat().st_mtime_ns)

    def test_installed_codex_copy_selects_context_without_ringer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            installed = self.run_script(INSTALLER, "--home", str(home))
            self.assertEqual(0, installed.returncode, installed.stderr)
            project = root / "unrelated-project"
            project.mkdir()
            source = project / "notes.md"
            source.write_text(
                ("Unrelated material.\n" * 500)
                + "The standalone answer is Wednesday.\n"
                + ("More unrelated material.\n" * 500),
                encoding="utf-8",
            )
            selector = (
                home
                / ".agents"
                / "skills"
                / "token-saver"
                / "scripts"
                / "select_context.py"
            )

            result = self.run_script(
                selector,
                "--request",
                "What is the standalone answer?",
                "--root",
                str(project),
                "--max-packet-bytes",
                "3000",
                cwd=project,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("standalone answer is Wednesday", result.stdout)

    def test_installer_refuses_conflict_until_force_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            home = Path(temp_root) / "home"
            first = self.run_script(INSTALLER, "--home", str(home))
            self.assertEqual(0, first.returncode, first.stderr)
            codex_skill = home / ".agents" / "skills" / "token-saver" / "SKILL.md"
            codex_skill.write_text("different local skill\n", encoding="utf-8")

            refused = self.run_script(INSTALLER, "--home", str(home))
            self.assertEqual(2, refused.returncode)
            self.assertIn("Nothing was changed", refused.stderr)
            self.assertEqual(
                "different local skill\n",
                codex_skill.read_text(encoding="utf-8"),
            )

            forced = self.run_script(
                INSTALLER,
                "--home",
                str(home),
                "--force",
            )
            self.assertEqual(0, forced.returncode, forced.stderr)
            self.assertEqual(
                (SKILL / "SKILL.md").read_bytes(),
                codex_skill.read_bytes(),
            )

    def test_readme_has_one_command_install_without_ringer(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("python3 scripts/install_token_saver.py", readme)
        self.assertIn("installing the Ringer gateway", readme)
        self.assertIn("~/.agents/skills/token-saver", readme)
        self.assertIn("~/.claude/skills/token-saver", readme)


if __name__ == "__main__":
    unittest.main(verbosity=2)
