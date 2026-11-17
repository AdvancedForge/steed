"""Retirement metadata preserves legacy stores and isolates delayed engine I/O."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser.config import Config
from browser.utils import save_json
from services.profile_storage import MANIFEST, ProfileStorage


class ProfileStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        with patch.object(Path, "home", return_value=Path(self.directory.name)):
            self.config = Config()
        self.storage = ProfileStorage(self.config)
        self.profile = self.config.profiles_dir / "work"

    def test_legacy_store_keeps_its_paths_and_contents(self):
        (self.profile / "storage").mkdir(parents=True)
        marker = self.profile / "storage" / "Cookies"
        marker.write_bytes(b"synthetic legacy database")
        storage, cache = self.storage.prepare("work", self.profile)
        self.assertEqual(storage, self.profile / "storage")
        self.assertEqual(cache, self.profile / "cache")
        self.assertEqual(marker.read_bytes(), b"synthetic legacy database")
        self.storage.retire("work", self.profile)
        self.assertTrue(self.storage.active("work", self.profile))
        self.assertEqual(self.storage.prepare("work", self.profile)[0], storage)

    def test_fresh_identity_paths_are_stable_on_restart(self):
        paths = self.storage.prepare("work", self.profile, new=True)
        metadata = self.storage.manifest(self.profile)
        self.assertTrue(metadata["isolated"])
        self.assertEqual(paths[0], self.profile / "storage" / metadata["identity"])
        restarted = ProfileStorage(self.config)
        self.assertEqual(restarted.prepare("work", self.profile), paths)

    def test_late_recreated_directory_is_not_an_active_profile_after_restart(self):
        old_storage, _ = self.storage.prepare("work", self.profile, new=True)
        self.storage.retire("work", self.profile)
        archived = self.config.config_dir / "archive"
        self.profile.rename(archived)
        old_storage.mkdir(parents=True)
        (old_storage / "late-log").write_text("synthetic")
        restarted = ProfileStorage(self.config)
        self.assertFalse(restarted.active("work", self.profile))
        with self.assertRaisesRegex(ValueError, "archived"):
            restarted.prepare("work", self.profile)
        self.assertTrue((archived / MANIFEST).exists())

    def test_same_name_recreation_preserves_remnants_and_uses_a_new_engine_path(self):
        old_storage, _ = self.storage.prepare("work", self.profile, new=True)
        self.storage.retire("work", self.profile)
        self.profile.rename(self.config.config_dir / "archive")
        old_storage.mkdir(parents=True)
        (self.profile / "unexpected-owner-file").write_text("keep this")
        new_storage, _ = self.storage.prepare("work", self.profile, new=True)
        self.assertNotEqual(new_storage, old_storage)
        old_storage.mkdir(parents=True, exist_ok=True)
        (old_storage / "late-log").write_text("old engine")
        new_storage.mkdir(parents=True)
        (new_storage / "new-log").write_text("new engine")
        self.assertTrue(self.storage.active("work", self.profile))
        self.assertEqual(self.storage.prepare("work", self.profile)[0], new_storage)
        self.assertFalse((new_storage / "late-log").exists())
        copies = list(
            (self.config.config_dir / "deleted_profiles").glob(
                "work-late-*/unexpected-owner-file"
            )
        )
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_text(), "keep this")

    def test_restoring_complete_archive_makes_legacy_profile_visible(self):
        self.profile.mkdir()
        (self.profile / "owner-file").write_text("restore me")
        self.storage.retire("work", self.profile)
        archive = self.config.config_dir / "archive"
        self.profile.rename(archive)
        shutil.copytree(archive, self.profile)
        self.assertTrue(ProfileStorage(self.config).active("work", self.profile))
        self.assertEqual((self.profile / "owner-file").read_text(), "restore me")

    def test_failed_index_write_leaves_original_profile_visible_and_data_intact(self):
        self.profile.mkdir()
        (self.profile / "owner-file").write_text("keep me")

        def fail_index(path, value):
            if path == self.storage.index:
                raise OSError("synthetic")
            save_json(path, value)

        with patch("services.profile_storage.save_json", side_effect=fail_index):
            with self.assertRaises(OSError):
                self.storage.retire("work", self.profile)
        self.assertTrue(ProfileStorage(self.config).active("work", self.profile))
        self.assertEqual((self.profile / "owner-file").read_text(), "keep me")

    def test_malformed_manifest_or_index_is_not_silently_overwritten(self):
        self.profile.mkdir()
        marker = self.profile / MANIFEST
        for payload in (
            "bad json",
            "{}",
            json.dumps({"version": True, "identity": "a" * 32, "isolated": True}),
        ):
            marker.write_text(payload)
            with self.assertRaises(ValueError):
                self.storage.prepare("work", self.profile)
            self.assertEqual(marker.read_text(), payload)
        self.storage.index.write_text('{"unexpected":true}')
        with self.assertRaises(ValueError):
            ProfileStorage(self.config)
