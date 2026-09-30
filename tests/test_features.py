"""Pure policy and filtering regressions (no browser initialization required)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtNetwork import QNetworkCookie

from ad_blocker import FilterEngine
from config import Config
from cookie_sharing import CookieSharing, cookie_key
from download_manager import DownloadManager
from tab_manager import ClosedTabHistory, TabState
from utils import load_json, save_json


def cookie(value=b"global", domain=".example.org"):
    result = QNetworkCookie(b"session", value)
    result.setDomain(domain)
    result.setPath("/")
    return result


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        home.start()
        self.addCleanup(home.stop)
        self.config = Config()

    def sharing(self, fresh=True):
        sharing = CookieSharing(self.config, Mock())
        profile = Mock()
        directory = self.config.profiles_dir / "work"
        directory.mkdir()
        sharing.register("work", profile, directory, fresh)
        sharing.enable("work")
        return sharing, profile.cookieStore.return_value

    def test_closed_tabs_are_bounded_and_profile_isolated(self):
        history = ClosedTabHistory(limit=2)
        for index in range(3):
            history.push("work", TabState(str(index), str(index), "Group"), index)
        self.assertIsNone(history.pop("other"))
        self.assertEqual(history.pop("work").index, 2)
        self.assertEqual(history.pop("work").state.url, "1")
        self.assertIsNone(history.pop("work"))

    def test_global_update_preserves_local_login(self):
        sharing, store = self.sharing()
        original = cookie()
        sharing.global_added(original)
        store.setCookie.assert_called_once()
        sharing.target_added("work", original)
        sharing.target_added("work", cookie(b"local"))
        sharing.global_added(cookie(b"new-global"))
        self.assertEqual(store.setCookie.call_count, 1)
        sharing.global_removed(original)
        store.deleteCookie.assert_not_called()

    def test_global_deletion_only_removes_inherited(self):
        sharing, store = self.sharing()
        original = cookie()
        sharing.global_added(original)
        sharing.target_added("work", original)
        sharing.global_removed(original)
        store.deleteCookie.assert_called_once()
        sharing.target_removed("work", original)
        self.assertFalse(sharing.local("work", "example.org"))

    def test_local_deletion_does_not_resurrect(self):
        sharing, store = self.sharing()
        original = cookie()
        sharing.global_added(original)
        sharing.target_added("work", original)
        sharing.protect_deletion("work", [original])
        sharing.target_removed("work", original)
        sharing.reconcile("work")
        self.assertEqual(store.setCookie.call_count, 1)

    def test_migration_does_not_assume_existing_store_empty(self):
        sharing, store = self.sharing(fresh=False)
        sharing.global_added(cookie())
        store.setCookie.assert_not_called()
        sharing.use_global("work", "example.org")
        store.setCookie.assert_called_once()

    def test_no_plaintext_cookie_in_journal(self):
        sharing, store = self.sharing()
        sharing.global_added(cookie(b"top-secret-cookie"))
        data = sharing.paths["work"].read_text()
        self.assertNotIn("top-secret-cookie", data)
        self.assertIn("inherited", data)

    def test_unknown_origin_not_guessed(self):
        sharing, store = self.sharing()
        sharing.global_added(cookie(domain=""))
        self.assertEqual(sharing.global_cookies, {})
        store.setCookie.assert_not_called()

    def test_host_only_copy_preserves_scope(self):
        sharing, store = self.sharing()
        sharing.global_added(cookie(domain="example.org"))
        copy, origin = store.setCookie.call_args.args
        self.assertEqual(copy.domain(), "")
        self.assertEqual(origin.host(), "example.org")

    def test_late_inherited_cookie_obeys_global_deletion(self):
        sharing, store = self.sharing()
        original = cookie()
        sharing.global_added(original)
        sharing.global_removed(original)
        sharing.target_added("work", original)
        store.deleteCookie.assert_called_once()
        self.assertIn(cookie_key(original), load_json(sharing.deletions_file))

    def test_download_restart_marks_unfinished_interrupted(self):
        save_json(
            self.config.config_dir / "downloads.json",
            [
                {
                    "download_id": "old",
                    "name": "file",
                    "path": "/tmp/file",
                    "profile": "work",
                }
            ],
        )
        manager = DownloadManager(self.config)
        self.assertEqual(manager.records["old"].state, "Interrupted")
        self.assertFalse(manager.active())

    def test_filter_network_exception_and_cosmetic(self):
        engine = FilterEngine(
            [
                "||ads.example^",
                "@@||ads.example/allowed.js$script",
                "example.org##.advert",
                "##.generic-ad",
            ]
        )
        self.assertTrue(
            engine.blocked(
                "https://ads.example/banner.js", "https://example.org", "script"
            )
        )
        self.assertFalse(
            engine.blocked(
                "https://ads.example/allowed.js", "https://example.org", "script"
            )
        )
        self.assertIn(".advert", engine.selectors("https://example.org"))
        self.assertIn(
            ".generic-ad", engine.selectors("https://example.org", ["generic-ad"])
        )
