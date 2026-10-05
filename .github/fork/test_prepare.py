import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from prepare import (SOURCE_INPUTS, UPSTREAM, asset_names, check_existing_source,
                     check_published_assets, release_by_tag, source_fingerprint, stable_tag)


class DraftReleaseTest(unittest.TestCase):
    def test_lookup_uses_release_id_and_only_not_found_is_optional(self):
        with patch("prepare.subprocess.run") as command, patch("prepare.api") as api:
            command.return_value = Mock(returncode=0, stdout=json.dumps({
                "apiUrl": "https://api.github.com/repos/owner/core/releases/123"}))
            api.return_value = {"draft": True, "assets": []}
            self.assertTrue(release_by_tag("owner/core", "v1.2.3")["draft"])
            api.assert_called_once_with("repos/owner/core/releases/123")
            command.return_value = Mock(returncode=1, stderr="release not found\n")
            self.assertIsNone(release_by_tag("owner/core", "v1.2.3"))
            command.return_value = Mock(returncode=1, stderr="HTTP 403: forbidden")
            with self.assertRaises(RuntimeError):
                release_by_tag("owner/core", "v1.2.3")


class StableReleaseTest(unittest.TestCase):
    def test_only_published_stable_tags(self):
        self.assertEqual(stable_tag({"tag_name": "v1.19.32"}), "v1.19.32")
        for invalid in [None, {"tag_name": "v1.19.32", "draft": True},
                        {"tag_name": "v1.19.32", "prerelease": True},
                        {"tag_name": "Alpha"}, {"tag_name": "v1.2.3\nunsafe"}]:
            with self.assertRaises(ValueError):
                stable_tag(invalid)


class PublishedReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tag = "v1.19.32"
        self.release = {"assets": [{"name": name, "size": 1, "state": "uploaded"}
                                   for name in asset_names(self.tag, metadata=True)]}

    def test_complete_historical_release_needs_no_new_fingerprint(self):
        check_published_assets(self.release, self.tag)

    def test_missing_extra_or_duplicate_asset_is_rejected(self):
        variants = [self.release["assets"][:-1], self.release["assets"] + [
            {"name": "unexpected.zip", "size": 1, "state": "uploaded"}],
            self.release["assets"] + [self.release["assets"][0]]]
        for assets in variants:
            with self.subTest(assets=assets), self.assertRaises(RuntimeError):
                check_published_assets({"assets": assets}, self.tag)

    def test_empty_or_unfinished_asset_is_rejected(self):
        for change in [{"size": 0}, {"state": "starter"}]:
            release = copy.deepcopy(self.release)
            release["assets"][0].update(change)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                check_published_assets(release, self.tag)


class ExistingSourceTest(unittest.TestCase):
    def setUp(self):
        self.tag, self.repository, self.fingerprint = "v1.19.32", "owner/core", "a" * 64
        self.record = {"upstream_repository": UPSTREAM, "upstream_tag": self.tag,
                       "upstream_sha": "b" * 40, "anytls_reality": True,
                       "runtime_update_repository": self.repository,
                       "source_fingerprint": self.fingerprint}

    def check(self, record):
        contents = {"encoding": "base64", "content": base64.encodebytes(
            json.dumps(record).encode("utf-8")).decode("ascii")}
        check_existing_source(contents, self.tag, self.repository, self.fingerprint)

    def test_matching_source_is_reusable(self):
        self.check(self.record)

    def test_missing_or_changed_provenance_is_rejected(self):
        for key in self.record:
            for value in [None, "changed"]:
                record = dict(self.record)
                if value is None:
                    del record[key]
                else:
                    record[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                    self.check(record)

    def test_malformed_provenance_is_rejected(self):
        for record in [[], None, "invalid", {**self.record, "anytls_reality": 1}]:
            with self.subTest(record=record), self.assertRaises(RuntimeError):
                self.check(record)
        for contents in [None, {"encoding": "none"}, {"encoding": "base64", "content": "not json"}]:
            with self.subTest(contents=contents), self.assertRaises(RuntimeError):
                check_existing_source(contents, self.tag, self.repository, self.fingerprint)


class SourceFingerprintTest(unittest.TestCase):
    def test_source_changes_matter_but_line_endings_and_docs_do_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in SOURCE_INPUTS:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"source\n")
            original = source_fingerprint(root)
            for name in SOURCE_INPUTS:
                (root / name).write_bytes(b"source\r\n")
            (root / "ANYTLS-REALITY.md").write_text("Changed documentation", encoding="utf-8")
            self.assertEqual(source_fingerprint(root), original)
            for name in SOURCE_INPUTS:
                (root / name).write_bytes(b"changed\n")
                self.assertNotEqual(source_fingerprint(root), original, name)
                (root / name).write_bytes(b"source\n")


if __name__ == "__main__":
    unittest.main()
