"""Ringside resources, native CORS boundary, selected-folder routing and JS behavior."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ringer  # noqa: E402


class RingsideFrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env_patch = patch.dict(os.environ, {"RINGER_HOME":str(self.root / "home")})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.server = ringer.PersistentHudServer(self.root, preferred_port=0, open_viewer=False)
        self.base = f"http://127.0.0.1:{self.server.start()}"
        self.addCleanup(self.server.stop)

    def test_shared_resources_and_native_origin_are_allowlisted(self) -> None:
        for path, content_type in [("/ringside.js","text/javascript"),("/ringside.css","text/css"),("/hud.js","text/javascript"),("/assets/ringside-mark.svg","image/svg+xml")]:
            with urlopen(self.base+path, timeout=5) as response:
                self.assertTrue(response.headers["Content-Type"].startswith(content_type))
                self.assertGreater(len(response.read()), 20)
        for origin, permitted in [("tauri://localhost",True),("https://tauri.localhost",True),("https://untrusted.example",False)]:
            with urlopen(Request(self.base+"/api/runs", headers={"Origin":origin}), timeout=5) as response:
                self.assertEqual(origin if permitted else None, response.headers.get("Access-Control-Allow-Origin"))
                self.assertEqual([], json.loads(response.read())["runs"])
        with self.assertRaises(Exception):
            urlopen(self.base+"/assets/../../ringer.py", timeout=5)

    def test_open_folder_uses_selected_version_run(self) -> None:
        directory = self.root / "artifacts" / "deliverables" / "saved-run"
        directory.mkdir(parents=True)
        with patch.object(ringer.sys,"platform","darwin"), patch.object(ringer.subprocess,"Popen") as popen:
            with urlopen(Request(self.base+"/api/open-folder?artifact=Release&run=saved-run", headers={"Origin":"tauri://localhost"}), timeout=5) as response:
                self.assertEqual(204,response.status)
                self.assertEqual("tauri://localhost",response.headers.get("Access-Control-Allow-Origin"))
            self.assertEqual(["open",str(directory.resolve())],popen.call_args.args[0])

    @unittest.skipUnless(shutil.which("node"), "Node is optional; run the browser recipe in TESTING.md")
    def test_frontend_behavior_without_dependencies(self) -> None:
        result = subprocess.run(["node",str(ROOT / "tests" / "ringside_frontend_test.js")],capture_output=True,text=True,timeout=15)
        self.assertEqual(0,result.returncode,result.stdout+result.stderr)
        self.assertIn("PASS:",result.stdout)


if __name__ == "__main__":
    unittest.main()
