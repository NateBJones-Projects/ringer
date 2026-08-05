#!/usr/bin/env python3
"""Anti-dark-code check in the fix-swarm kit: every diff hunk must cite a declared item ID."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK_PATH = ROOT / "templates" / "fix-swarm" / "checks" / "fix-swarm.py"


def load_check_module():
    spec = importlib.util.spec_from_file_location("fix_swarm_check", CHECK_PATH)
    assert spec and spec.loader, f"could not load {CHECK_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TAGGED_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,6 +10,7 @@ def handler():
     ctx = build()
+    # R17-P0.1: propagate the request id
+    ctx.request_id = incoming.id
     return ctx
"""

# The second hunk is the scope creep: a real change nobody asked for.
MIXED_DIFF = TAGGED_DIFF + """diff --git a/embeddings.py b/embeddings.py
--- a/embeddings.py
+++ b/embeddings.py
@@ -40,5 +40,9 @@ def embed(text):
-    return legacy_embed(text)
+    return streaming_embed(text)
"""


class FixSwarmItemIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.check = load_check_module()

    def test_hunk_citing_a_declared_id_passes(self) -> None:
        self.assertEqual([], self.check.find_untagged_hunks(TAGGED_DIFF, ["R17-P0.1"]))

    def test_untagged_hunk_is_reported_with_file_and_header(self) -> None:
        untagged = self.check.find_untagged_hunks(MIXED_DIFF, ["R17-P0.1"])
        self.assertEqual(1, len(untagged), f"expected exactly the unrequested hunk: {untagged}")
        self.assertIn("embeddings.py", untagged[0])
        self.assertIn("@@", untagged[0])

    def test_check_is_opt_in(self) -> None:
        """No declared IDs disables it, so kits used by other projects are unaffected."""
        self.assertEqual([], self.check.find_untagged_hunks(MIXED_DIFF, []))

    def test_any_one_of_several_declared_ids_satisfies_a_hunk(self) -> None:
        self.assertEqual([], self.check.find_untagged_hunks(TAGGED_DIFF, ["R17-P9.9", "R17-P0.1"]))

    def test_id_in_a_context_line_counts(self) -> None:
        """A worker may cite the ID in a comment near, not inside, the changed lines."""
        context_diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,6 +10,7 @@ def handler():
     # R17-P0.1: the fix below closes this item
     ctx = build()
+    ctx.request_id = incoming.id
     return ctx
"""
        self.assertEqual([], self.check.find_untagged_hunks(context_diff, ["R17-P0.1"]))


if __name__ == "__main__":
    unittest.main()
