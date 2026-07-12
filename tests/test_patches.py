import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import patches


HAVE_LOGIC = any(os.path.isdir(r) for r in patches.PATCH_ROOTS)


@unittest.skipUnless(HAVE_LOGIC, "Logic Pro factory patches not installed")
class TestFindPatches(unittest.TestCase):
    def test_index_builds(self):
        self.assertGreater(len(patches._build_index()), 100)

    def test_limit(self):
        self.assertEqual(len(patches.find_patches(limit=5)), 5)

    def test_name_filter(self):
        for hit in patches.find_patches(query="bass", limit=10):
            self.assertIn("bass", hit["name"].lower())

    def test_category_filter(self):
        for hit in patches.find_patches(category="Synthesizer", limit=10):
            self.assertIn("synthesizer", hit["category"].lower())

    def test_plugin_filter_finds_alchemy(self):
        hits = patches.find_patches(category="Synthesizer/Pad", plugin="Alchemy", limit=3)
        self.assertTrue(hits)


class TestOffline(unittest.TestCase):
    def test_missing_roots_ok(self):
        old_roots, old_index = patches.PATCH_ROOTS, patches._index
        patches.PATCH_ROOTS, patches._index = ["/nonexistent"], None
        try:
            self.assertEqual(patches.find_patches(query="x"), [])
        finally:
            patches.PATCH_ROOTS, patches._index = old_roots, old_index


if __name__ == "__main__":
    unittest.main()
