from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "engines" / "opencode-sandboxed.sh"

# A stand-in for the opencode binary. It runs inside the Seatbelt sandbox and
# reports what OpenCode would see: the models.json in its cache dir, and
# whether it can write to the host cache file.
FAKE_OPENCODE = """#!/bin/bash
models="$XDG_CACHE_HOME/opencode/models.json"
if [ -f "$models" ]; then echo "SEEN:$(cat "$models")"; else echo "SEEN:MISSING"; fi
if [ -n "${HOST_MODELS_TO_TOUCH:-}" ]; then
  if echo tampered > "$HOST_MODELS_TO_TOUCH" 2>/dev/null; then echo "HOST:WRITABLE"; else echo "HOST:BLOCKED"; fi
fi
"""


@unittest.skipUnless(
    sys.platform == "darwin" and os.access("/usr/bin/sandbox-exec", os.X_OK),
    "opencode-sandboxed.sh needs macOS sandbox-exec",
)
class OpenCodeSandboxedModelsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="ringer-oc-sandbox-")
        self.root = Path(self.tmp.name).resolve()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        fake = self.bin_dir / "opencode"
        fake.write_text(FAKE_OPENCODE)
        fake.chmod(0o755)
        self.home = self.root / "home"
        self.home.mkdir()
        self.host_cache = self.root / "host-cache"
        self.taskdir = self.root / "task"
        self.taskdir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_wrapper(self, extra_env: dict[str, str] | None = None) -> str:
        env = {
            "PATH": f"{self.bin_dir}:/usr/bin:/bin",
            "HOME": str(self.home),
            "XDG_CACHE_HOME": str(self.host_cache),
        }
        env.update(extra_env or {})
        result = subprocess.run(
            ["/bin/bash", str(WRAPPER), str(self.taskdir), "run"],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, f"wrapper failed: {result.stderr}")
        return result.stdout

    def write_host_models(self, text: str) -> Path:
        models = self.host_cache / "opencode" / "models.json"
        models.parent.mkdir(parents=True)
        models.write_text(text)
        return models

    def test_sandboxed_worker_sees_the_host_models_list(self) -> None:
        self.write_host_models('{"marker":"refreshed-list"}')
        out = self.run_wrapper()
        self.assertIn('SEEN:{"marker":"refreshed-list"}', out)

    def test_missing_host_models_list_is_not_an_error(self) -> None:
        out = self.run_wrapper()
        self.assertIn("SEEN:MISSING", out)

    def test_sandboxed_worker_cannot_write_the_host_models_list(self) -> None:
        models = self.write_host_models('{"marker":"original"}')
        out = self.run_wrapper({"HOST_MODELS_TO_TOUCH": str(models)})
        self.assertIn("HOST:BLOCKED", out)
        self.assertEqual(models.read_text(), '{"marker":"original"}')


if __name__ == "__main__":
    unittest.main()
