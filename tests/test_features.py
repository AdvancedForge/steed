"""Pure policy and filtering regressions (no browser initialization required)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtNetwork import QNetworkCookie

from ad_blocker import FilterEngine
from config import Config
from cookie_sharing import CookieSharing, cookie_key
from cookie_snapshot import VERIFIED_AGAINST_QT, CookieSnapshot
from download_manager import DownloadManager
from tab_manager import ClosedTabHistory, TabState
from utils import load_json, save_json


def cookie(value=b"global", domain=".example.org"):
    result = QNetworkCookie(b"session", value)
    result.setDomain(domain)
    result.setPath("/")
    return result


class CookieSnapshotPolicyTests(unittest.TestCase):
    """Refresh scheduling, which no longer waits on a live engine."""

    def snapshot(self):
        snapshot = CookieSnapshot.__new__(CookieSnapshot)
        snapshot.closed = False
        snapshot.ready = True
        snapshot.pending = False
        snapshot.dirty = False
        snapshot.attempts = 0
        snapshot.revision = 0
        snapshot.request_revision = 0
        snapshot.retries = 0
        snapshot.cooldowns = 0
        snapshot.timeouts = 0
        snapshot.timed_out = False
        snapshot.retryable_error = False
        snapshot.skipped_partitioned = 0
        snapshot.frontend = Mock()
        snapshot.timer = Mock()
        snapshot.loaded = Mock()
        snapshot.failed = Mock()
        return snapshot

    def test_refresh_requested_during_a_pending_run_is_not_dropped(self):
        """Qt reports a cookie overwrite while a run is still in flight.

        Dropping that request loses the update with nothing left to retrigger
        it, which is how an overwritten global cookie stayed stale forever.
        """
        snapshot = self.snapshot()
        snapshot.pending = True
        snapshot.dirty = False
        CookieSnapshot.refresh(snapshot)
        self.assertTrue(snapshot.dirty, "a queued refresh must be recorded")

    def test_stale_result_is_discarded_when_a_change_overtook_it(self):
        snapshot = self.snapshot()
        snapshot.pending = True
        snapshot.dirty = True
        snapshot.revision = 2
        snapshot.request_revision = 1
        with patch.object(
            CookieSnapshot, "refresh", lambda self: setattr(self, "requeued", True)
        ):
            CookieSnapshot.accept_result(snapshot, {"cookies": []})
        self.assertTrue(
            getattr(snapshot, "requeued", False),
            "a result that lost the race must trigger another run",
        )
        self.assertFalse(snapshot.pending, "the run must no longer be pending")


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
        self.assertEqual(store.setCookie.call_count, 2)
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
        self.assertEqual(store.setCookie.call_count, 2)
        self.assertIn(cookie_key(original), load_json(sharing.deletions_file))

    def test_clear_all_then_reset_one_site_keeps_other_sites_blocked(self):
        sharing, store = self.sharing()
        sharing.global_added(cookie())
        sharing.global_added(cookie(domain=".other.example"))
        sharing.protect_deletion("work")
        sharing.use_global("work", "example.org")
        self.assertFalse(sharing.local("work", "example.org"))
        self.assertTrue(sharing.local("work", "other.example"))

    def test_inheritance_disabled_does_not_process_startup_tombstones(self):
        sharing, store = self.sharing()
        original = cookie()
        sharing.global_added(original)
        sharing.enabled.clear()
        sharing.global_removed(original)
        sharing.target_added("work", original)
        self.assertEqual(store.setCookie.call_count, 1)
        sharing.enable("work")
        self.assertEqual(store.setCookie.call_count, 2)

    def test_cookie_snapshot_records_build_identity(self):
        """The snapshot path relies on private Chromium internals.

        Record the engine version it was last confirmed against so a Qt upgrade
        review knows exactly what to re-verify.
        """
        self.assertRegex(VERIFIED_AGAINST_QT, r"^\d+\.\d+\.\d+$")

    def test_download_abandoned_prompt_cancels_and_cleans_up(self):
        manager = DownloadManager(self.config)
        request = Mock()
        request.page.return_value = None
        request.suggestedFileName.return_value = "abandoned.bin"
        with patch(
            "download_manager.QFileDialog.getSaveFileName", return_value=("", "")
        ):
            manager.handle(request, "default")
        request.cancel.assert_called_once()
        request.deleteLater.assert_called_once()
        self.assertEqual(manager.records, {})
        self.assertEqual(manager.requests, {})

    def test_download_dialog_failure_still_cancels_request(self):
        manager = DownloadManager(self.config)
        request = Mock()
        request.page.return_value = None
        request.suggestedFileName.return_value = "boom.bin"
        with (
            patch(
                "download_manager.QFileDialog.getSaveFileName",
                side_effect=RuntimeError("no file dialog"),
            ),
            self.assertLogs("download_manager", level="ERROR"),
        ):
            manager.handle(request, "default")
        request.cancel.assert_called_once()
        self.assertEqual(manager.records, {})

    def test_closed_sharing_does_not_enable_new_profiles(self):
        config = self.config
        config.update(
            {
                "global_cookie_sharing_mode": "all_profiles",
                "global_cookie_sharing_apply_on_startup": True,
            }
        )
        sharing = CookieSharing(config, Mock())
        sharing.register("work", Mock(), config.profiles_dir / "work", fresh=True)
        sharing.closed = True
        sharing.enable("work")
        self.assertEqual(sharing.enabled, set(), "a closed registry must not resume")
        self.assertEqual(sharing.states["work"]["inherited"], {})

    def test_cookie_sharing_reconcile_stops_when_closed(self):
        sharing, store = self.sharing()
        sharing.global_added(cookie())
        sharing.closed = True
        sharing.enabled.clear()
        before = store.setCookie.call_count
        sharing.reconcile("work")
        sharing.use_global("work", "example.org")
        sharing.enable("work")
        self.assertEqual(store.setCookie.call_count, before)
        self.assertEqual(sharing.enabled, set())

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
