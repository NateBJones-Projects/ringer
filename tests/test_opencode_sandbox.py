from __future__ import annotations

import os
import platform
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "engines" / "opencode-sandboxed.sh"
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")


@unittest.skipUnless(
    platform.system() == "Darwin" and SANDBOX_EXEC.is_file(),
    "requires macOS sandbox-exec",
)
class OpenCodeSandboxTests(unittest.TestCase):
    def test_task_can_read_itself_and_external_inputs_but_not_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = (Path(root_text).resolve() / "root (quoted')")
            root.mkdir()
            workdir = root / "work"
            taskdir = workdir / "task-a"
            sibling = workdir / "task-b"
            external = root / "reference"
            fake_bin = root / "bin"
            for path in (taskdir, sibling, external, fake_bin):
                path.mkdir(parents=True)

            own_file = taskdir / "own.txt"
            sibling_file = sibling / "peer.txt"
            external_file = external / "reference.txt"
            own_file.write_text("own\n", encoding="utf-8")
            sibling_file.write_text("peer\n", encoding="utf-8")
            external_file.write_text("reference\n", encoding="utf-8")
            sibling_link = taskdir / "peer-link.txt"
            sibling_link.symlink_to(sibling_file)
            node_probe = taskdir / "node-probe.mjs"
            node_probe.write_text(
                'import assert from "node:assert/strict"; assert.equal(1, 1);\n',
                encoding="utf-8",
            )

            fake_opencode = fake_bin / "opencode"
            fake_opencode.write_text(
                """#!/bin/bash
set -u
taskdir="$1"
sibling_file="$2"
external_file="$3"
sibling_link="$4"

cat "$taskdir/own.txt" || exit 10
cat "$external_file" || exit 11
node --test "$taskdir/node-probe.mjs" || exit 13
printf 'created\\n' > "$taskdir/created.txt" || exit 12

if cat "$sibling_file" >/dev/null 2>&1; then
  echo sibling-read-leaked >&2
  exit 20
fi
if cat "$sibling_link" >/dev/null 2>&1; then
  echo symlink-read-leaked >&2
  exit 21
fi
if printf 'escaped\\n' > "$(dirname "$sibling_file")/escaped.txt" 2>/dev/null; then
  echo sibling-write-leaked >&2
  exit 22
fi

printf 'sandbox-ok\\n'
""",
                encoding="utf-8",
            )
            fake_opencode.chmod(fake_opencode.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(fake_bin), env.get("PATH", "")))
            result = subprocess.run(
                [
                    str(WRAPPER),
                    str(taskdir),
                    str(taskdir),
                    str(sibling_file),
                    str(external_file),
                    str(sibling_link),
                ],
                cwd=taskdir,
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("own", result.stdout)
            self.assertIn("reference", result.stdout)
            self.assertIn("sandbox-ok", result.stdout)
            self.assertEqual((taskdir / "created.txt").read_text(), "created\n")
            self.assertFalse((sibling / "escaped.txt").exists())


if __name__ == "__main__":
    unittest.main()
