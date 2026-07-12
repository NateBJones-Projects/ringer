import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest import mock

from ringer import create_demo_manifest


ROOT = Path(__file__).resolve().parents[1]


class DemoManifestTests(unittest.TestCase):
    def test_task_specs_disambiguate_literal_content_from_punctuation(self) -> None:
        temp_root = ROOT / ".test-tmp"
        temp_root.mkdir(exist_ok=True)

        def create_workspace_temp(*, prefix: str) -> str:
            path = temp_root / f"{prefix}{uuid.uuid4().hex}"
            path.mkdir()
            return str(path)

        with mock.patch("ringer.tempfile.mkdtemp", side_effect=create_workspace_temp):
            manifest_path = create_demo_manifest()
        self.addCleanup(shutil.rmtree, manifest_path.parent, ignore_errors=True)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        for task in manifest["tasks"]:
            expected = f"{task['key']} ready"
            self.assertIn(f'complete contents must be exactly "{expected}"', task["spec"])
            self.assertIn("There is no period in the file content", task["spec"])
            self.assertNotIn(f"exactly: {expected}.", task["spec"])


if __name__ == "__main__":
    unittest.main()
