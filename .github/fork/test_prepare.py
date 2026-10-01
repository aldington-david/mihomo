import unittest
from prepare import stable_tag


class StableReleaseTest(unittest.TestCase):
    def test_only_published_stable_tags(self):
        self.assertEqual(stable_tag({"tag_name": "v1.19.32"}), "v1.19.32")
        for invalid in [None, {"tag_name": "v1.19.32", "draft": True},
                        {"tag_name": "v1.19.32", "prerelease": True},
                        {"tag_name": "Alpha"}, {"tag_name": "v1.2.3\nunsafe"}]:
            with self.assertRaises(ValueError):
                stable_tag(invalid)


if __name__ == "__main__":
    unittest.main()
