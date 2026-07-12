#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTemporaryDirectory:
    def __init__(self) -> None:
        root = ROOT / ".test-tmp"
        root.mkdir(exist_ok=True)
        self.path = root / f"agent-install-{uuid.uuid4().hex}"
        self.path.mkdir()
        self.name = str(self.path)

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class AgentInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = WorkspaceTemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.ringer_home = Path(self.tmp.name) / "ringer-home"
        self.home.mkdir()

    def run_cli(self, *args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["USERPROFILE"] = str(self.home)
        env["RINGER_HOME"] = str(self.ringer_home)
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

    def test_codex_install_uses_agents_skill_and_codex_hooks(self) -> None:
        result = self.run_cli("install-agent", "--agent", "codex")
        self.assertEqual(0, result.returncode, result.stderr)

        skill = self.home / ".agents" / "skills" / "ringer" / "SKILL.md"
        self.assertTrue(skill.exists())
        metadata = skill.parent / "agents" / "openai.yaml"
        self.assertTrue(metadata.exists())
        self.assertEqual(
            (ROOT / ".claude" / "skills" / "ringer" / "agents" / "openai.yaml").read_text(),
            metadata.read_text(),
        )
        hooks = self.read_codex_hooks()["hooks"]
        pre_handler = hooks["PreToolUse"][0]["hooks"][0]
        post_handler = hooks["PostToolUse"][0]["hooks"][0]
        self.assertEqual("Bash", hooks["PreToolUse"][0]["matcher"])
        self.assertEqual("Edit|Write", hooks["PostToolUse"][0]["matcher"])
        self.assertIn("ringer_nudge.py", pre_handler["command"])
        self.assertIn("ringer_nudge.py", pre_handler["commandWindows"])
        self.assertIn("py -3", pre_handler["commandWindows"])
        self.assertIn("ringer_nudge.py", post_handler["commandWindows"])

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
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "echo keep-me"}],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

        first = self.run_cli("install-agent", "--agent", "codex")
        second = self.run_cli("install-agent", "--agent", "codex")
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        installed = self.read_codex_hooks()
        self.assertEqual(2, len(self.ringer_handlers(installed)))

        uninstall = self.run_cli("uninstall-agent", "--agent", "codex")
        self.assertEqual(0, uninstall.returncode, uninstall.stderr)
        after = self.read_codex_hooks()
        self.assertEqual({"keep": True}, after["custom"])
        self.assertEqual([], self.ringer_handlers(after))
        self.assertFalse((self.home / ".agents" / "skills" / "ringer").exists())

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

    def test_codex_project_variant_writes_under_temp_cwd(self) -> None:
        project = Path(self.tmp.name) / "codex-project"
        project.mkdir()
        os.symlink(ROOT / "ringer.py", project / "ringer.py")

        install = self.run_cli("install-agent", "--agent", "codex", "--project", cwd=project)
        self.assertEqual(0, install.returncode, install.stderr)
        self.assertTrue((project / ".agents" / "skills" / "ringer" / "SKILL.md").exists())
        self.assertTrue((project / ".codex" / "hooks.json").exists())
        self.assertFalse((self.home / ".agents").exists())

        uninstall = self.run_cli("uninstall-agent", "--agent", "codex", "--project", cwd=project)
        self.assertEqual(0, uninstall.returncode, uninstall.stderr)
        self.assertFalse((project / ".agents" / "skills" / "ringer").exists())
        hooks = self.read_codex_hooks(project)
        self.assertEqual([], self.ringer_handlers(hooks))


if __name__ == "__main__":
    unittest.main(verbosity=2)
