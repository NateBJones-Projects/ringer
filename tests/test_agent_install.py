#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import contextlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AgentInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.ringer_home = Path(self.tmp.name) / "ringer-home"
        self.home.mkdir()

    def run_cli(self, *args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["RINGER_HOME"] = str(self.ringer_home)
        env.pop("CODEX_HOME", None)
        return subprocess.run(
            [sys.executable, "ringer.py", *args],
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def read_settings(self, root: Path | None = None) -> dict[str, object]:
        base = self.home if root is None else root
        return json.loads((base / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def read_codex_hooks(self, root: Path | None = None) -> dict[str, object]:
        base = self.home if root is None else root
        return json.loads((base / ".codex" / "hooks.json").read_text(encoding="utf-8"))

    def ringer_handlers(self, settings: dict[str, object]) -> list[dict[str, object]]:
        handlers: list[dict[str, object]] = []
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            return handlers
        for groups in hooks.values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for handler in group.get("hooks", []):
                    if isinstance(handler, dict) and "ringer_nudge.py" in str(handler.get("command", "")):
                        handlers.append(handler)
        return handlers

    def test_fresh_install_creates_skill_copy_and_hook_entries(self) -> None:
        result = self.run_cli("install-agent")
        self.assertEqual(0, result.returncode, result.stderr)

        skill = self.home / ".claude" / "skills" / "ringer" / "SKILL.md"
        self.assertTrue(skill.exists())
        self.assertEqual((ROOT / ".claude" / "skills" / "ringer" / "SKILL.md").read_text(), skill.read_text())

        settings = self.read_settings()
        hooks = settings["hooks"]
        self.assertIsInstance(hooks, dict)
        self.assertEqual("Bash", hooks["PreToolUse"][0]["matcher"])
        self.assertEqual("command", hooks["PreToolUse"][0]["hooks"][0]["type"])
        self.assertIn("ringer_nudge.py", hooks["PreToolUse"][0]["hooks"][0]["command"])
        self.assertTrue(hooks["PreToolUse"][0]["hooks"][0]["command"].endswith(" pre-bash"))
        self.assertEqual("Edit|Write", hooks["PostToolUse"][0]["matcher"])
        self.assertIn("ringer_nudge.py", hooks["PostToolUse"][0]["hooks"][0]["command"])
        self.assertTrue(hooks["PostToolUse"][0]["hooks"][0]["command"].endswith(" post-edit"))

    def test_second_install_is_idempotent(self) -> None:
        first = self.run_cli("install-agent")
        self.assertEqual(0, first.returncode, first.stderr)
        settings_before = self.read_settings()

        second = self.run_cli("install-agent")
        self.assertEqual(0, second.returncode, second.stderr)
        settings_after = self.read_settings()

        self.assertEqual(settings_before, settings_after)
        self.assertEqual(2, len(self.ringer_handlers(settings_after)))

    def test_install_preserves_unrelated_hooks_and_settings_keys(self) -> None:
        claude = self.home / ".claude"
        claude.mkdir()
        settings_path = claude / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "echo unrelated",
                                    }
                                ],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

        result = self.run_cli("install-agent")
        self.assertEqual(0, result.returncode, result.stderr)

        settings = self.read_settings()
        self.assertEqual("dark", settings["theme"])
        pre_groups = settings["hooks"]["PreToolUse"]
        commands = [handler["command"] for group in pre_groups for handler in group["hooks"]]
        self.assertIn("echo unrelated", commands)
        self.assertEqual(1, len(list(claude.glob("settings.json.bak-*"))))

    def test_uninstall_removes_only_ringer_entries_and_skill_dir(self) -> None:
        install = self.run_cli("install-agent")
        self.assertEqual(0, install.returncode, install.stderr)
        settings_path = self.home / ".claude" / "settings.json"
        settings = self.read_settings()
        settings["hooks"]["PreToolUse"].append(
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": "echo keep-me",
                    }
                ],
            }
        )
        settings["custom"] = {"keep": True}
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

        uninstall = self.run_cli("uninstall-agent")
        self.assertEqual(0, uninstall.returncode, uninstall.stderr)

        after = self.read_settings()
        self.assertEqual({"keep": True}, after["custom"])
        self.assertEqual([], self.ringer_handlers(after))
        kept = [
            handler["command"]
            for group in after["hooks"]["PreToolUse"]
            for handler in group["hooks"]
        ]
        self.assertEqual(["echo keep-me"], kept)
        self.assertFalse((self.home / ".claude" / "skills" / "ringer").exists())

    def test_project_variant_writes_under_temp_cwd(self) -> None:
        project = Path(self.tmp.name) / "project"
        project.mkdir()
        os.symlink(ROOT / "ringer.py", project / "ringer.py")

        install = self.run_cli("install-agent", "--project", cwd=project)
        self.assertEqual(0, install.returncode, install.stderr)
        self.assertTrue((project / ".claude" / "skills" / "ringer" / "SKILL.md").exists())
        self.assertTrue((project / ".claude" / "settings.json").exists())
        self.assertFalse((self.home / ".claude").exists())

        uninstall = self.run_cli("uninstall-agent", "--project", cwd=project)
        self.assertEqual(0, uninstall.returncode, uninstall.stderr)
        self.assertFalse((project / ".claude" / "skills" / "ringer").exists())
        settings = self.read_settings(project)
        self.assertEqual([], self.ringer_handlers(settings))

    def test_fresh_codex_install_creates_skill_and_hooks(self) -> None:
        result = self.run_cli("install-agent", "--codex")
        self.assertEqual(0, result.returncode, result.stderr)

        source = ROOT / ".agents" / "skills" / "ringer"
        target = self.home / ".agents" / "skills" / "ringer"
        self.assertEqual((source / "SKILL.md").read_text(), (target / "SKILL.md").read_text())
        self.assertEqual(
            (source / "agents" / "openai.yaml").read_text(),
            (target / "agents" / "openai.yaml").read_text(),
        )

        hooks = self.read_codex_hooks()["hooks"]
        self.assertEqual("", hooks["PreToolUse"][0]["matcher"])
        self.assertTrue(hooks["PreToolUse"][0]["hooks"][0]["command"].endswith(" codex-pre-tool"))
        self.assertEqual("", hooks["PostToolUse"][0]["matcher"])
        self.assertTrue(hooks["PostToolUse"][0]["hooks"][0]["command"].endswith(" codex-post-tool"))
        self.assertIn("open /hooks", result.stdout)
        self.assertFalse((self.home / ".claude").exists())
        # Skills live under .agents, NOT under .codex/skills.
        self.assertFalse((self.home / ".codex" / "skills").exists())

    def test_codex_install_is_idempotent(self) -> None:
        first = self.run_cli("install-agent", "--codex")
        self.assertEqual(0, first.returncode, first.stderr)
        hooks_before = self.read_codex_hooks()

        second = self.run_cli("install-agent", "--codex")
        self.assertEqual(0, second.returncode, second.stderr)
        hooks_after = self.read_codex_hooks()

        self.assertEqual(hooks_before, hooks_after)
        self.assertEqual(2, len(self.ringer_handlers(hooks_after)))

    def test_codex_install_and_uninstall_preserve_unrelated_hooks(self) -> None:
        codex = self.home / ".codex"
        codex.mkdir()
        hooks_path = codex / "hooks.json"
        hooks_path.write_text(
            json.dumps(
                {
                    "custom": {"keep": True},
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "shell",
                                "hooks": [{"type": "command", "command": "echo keep-me"}],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

        install = self.run_cli("install-agent", "--codex")
        self.assertEqual(0, install.returncode, install.stderr)
        self.assertEqual(1, len(list(codex.glob("hooks.json.bak-*"))))

        uninstall = self.run_cli("uninstall-agent", "--codex")
        self.assertEqual(0, uninstall.returncode, uninstall.stderr)
        after = self.read_codex_hooks()
        self.assertEqual({"keep": True}, after["custom"])
        self.assertEqual([], self.ringer_handlers(after))
        self.assertEqual("echo keep-me", after["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        self.assertFalse((self.home / ".agents" / "skills" / "ringer").exists())

    def test_codex_project_variant_uses_project_agents_and_codex_dirs(self) -> None:
        project = Path(self.tmp.name) / "codex-project"
        project.mkdir()
        os.symlink(ROOT / "ringer.py", project / "ringer.py")

        install = self.run_cli("install-agent", "--codex", "--project", cwd=project)
        self.assertEqual(0, install.returncode, install.stderr)
        # Skill lands under <project>/.agents/skills/ringer.
        self.assertTrue((project / ".agents" / "skills" / "ringer" / "SKILL.md").exists())
        self.assertTrue((project / ".agents" / "skills" / "ringer" / "agents" / "openai.yaml").exists())
        # Hooks land under <project>/.codex/hooks.json.
        self.assertTrue((project / ".codex" / "hooks.json").exists())
        self.assertFalse((project / ".codex" / "skills").exists())
        self.assertFalse((self.home / ".codex").exists())
        self.assertFalse((self.home / ".agents").exists())

        uninstall = self.run_cli("uninstall-agent", "--codex", "--project", cwd=project)
        self.assertEqual(0, uninstall.returncode, uninstall.stderr)
        self.assertFalse((project / ".agents" / "skills" / "ringer").exists())
        self.assertEqual([], self.ringer_handlers(self.read_codex_hooks(project)))

    def test_codex_install_respects_codex_home_for_hooks_not_skills(self) -> None:
        codex_home = Path(self.tmp.name) / "codex-home"
        codex_home.mkdir()
        env_override = os.environ.copy()
        env_override["CODEX_HOME"] = str(codex_home)
        # run_cli resets env; inject CODEX_HOME by running subprocess directly.
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["RINGER_HOME"] = str(self.ringer_home)
        env["CODEX_HOME"] = str(codex_home)
        result = subprocess.run(
            [sys.executable, "ringer.py", "install-agent", "--codex"],
            cwd=str(ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

        # Hooks honored CODEX_HOME.
        hooks = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(2, len(self.ringer_handlers(hooks)))
        # Skills did NOT follow CODEX_HOME; they stayed under $HOME/.agents.
        self.assertTrue((self.home / ".agents" / "skills" / "ringer" / "SKILL.md").exists())
        self.assertFalse((codex_home / "skills").exists())
        self.assertFalse((self.home / ".codex").exists())

    def test_codex_project_install_from_ringer_repo_is_self_source_safe(self) -> None:
        # Installing --codex --project from the Ringer repo itself: the
        # canonical skill source IS the target, so no self-copy happens,
        # but hooks are still written. Snapshot the repo's .codex state so
        # running this test does not leave artifacts in the real checkout.
        repo_hooks_path = ROOT / ".codex" / "hooks.json"
        snapshot_existed = repo_hooks_path.exists()
        snapshot_text = repo_hooks_path.read_text(encoding="utf-8") if snapshot_existed else None
        existing_baks = set((ROOT / ".codex").glob("hooks.json.bak-*")) if (ROOT / ".codex").exists() else set()

        def restore_repo_hooks() -> None:
            if snapshot_existed:
                repo_hooks_path.write_text(snapshot_text, encoding="utf-8")
            else:
                with contextlib.suppress(FileNotFoundError):
                    repo_hooks_path.unlink()
            for bak in (ROOT / ".codex").glob("hooks.json.bak-*"):
                if bak not in existing_baks:
                    with contextlib.suppress(FileNotFoundError):
                        bak.unlink()

        self.addCleanup(restore_repo_hooks)

        install = self.run_cli("install-agent", "--codex", "--project", cwd=ROOT)
        self.assertEqual(0, install.returncode, install.stderr)
        self.assertIn("source is target", install.stdout)
        # The packaged canonical skill is intact.
        self.assertTrue((ROOT / ".agents" / "skills" / "ringer" / "SKILL.md").exists())
        # Hooks were written to the repo's .codex/hooks.json.
        repo_hooks = json.loads(repo_hooks_path.read_text(encoding="utf-8"))
        self.assertEqual(2, len(self.ringer_handlers(repo_hooks)))

        uninstall = self.run_cli("uninstall-agent", "--codex", "--project", cwd=ROOT)
        self.assertEqual(0, uninstall.returncode, uninstall.stderr)
        self.assertIn("source is target", uninstall.stdout)
        # Uninstall preserved the packaged skill...
        self.assertTrue((ROOT / ".agents" / "skills" / "ringer" / "SKILL.md").exists())
        # ...but removed the Ringer hook entries from the repo hooks file.
        repo_hooks_after = json.loads(repo_hooks_path.read_text(encoding="utf-8"))
        self.assertEqual([], self.ringer_handlers(repo_hooks_after))



if __name__ == "__main__":
    unittest.main(verbosity=2)
