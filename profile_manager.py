"""Window-local selection backed by an application-owned engine registry."""

import logging
from pathlib import Path
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication

from ad_blocker import AdBlocker
from browsing_history import BrowsingHistory
from config import Config
from cookie_sharing import CookieSharing, cookie_key, fingerprint
from cookie_snapshot import CookieSnapshot
from download_manager import DownloadManager
from password_manager import PasswordManager
from utils import get_profile_path, sanitize_profile_name
from webengine import configure_web_settings

logger = logging.getLogger(__name__)

# How many follow-up inspections confirm a still-changing global store before
# the reading is accepted as settled.
MAX_SNAPSHOT_DISAGREEMENTS = 3

# Delay before the confirming inspection, so a burst of writes can finish first.
SNAPSHOT_SETTLE_MS = 250


class ProfileRegistry(QObject):
    """One engine profile per storage path; last window flushes engine storage."""

    profiles_updated = pyqtSignal()

    def __init__(self, config, parent):
        super().__init__(parent)
        self.config = config
        self.profiles = {}
        self.bootstrap_pages = {}
        self.snapshots = {}
        self.defaults = {}
        self.histories = {}
        self.snapshot_disagreements = 0
        self.global_fingerprints = None
        self.owners = set()
        self.downloads = DownloadManager(config, self)
        self.passwords = PasswordManager(config, self)
        self.adblock = AdBlocker(config, self)
        self.global_profile = self.create(
            "global", config.config_dir / "global_profile"
        )
        self.search_profile = QWebEngineProfile(self)
        self.defaults["search"] = self.search_profile.httpUserAgent()
        self.search_profile.setHttpCacheType(
            QWebEngineProfile.HttpCacheType.MemoryHttpCache
        )
        configure_web_settings(self.search_profile.settings())
        self.downloads.attach(self.search_profile, "search")
        self.adblock.attach(self.search_profile)
        self.prepare_inspection("search", self.search_profile)
        # Confirm source identities through a full snapshot: live Qt signals
        # cannot distinguish partitioned cookies and overwrite notifications.
        self.sharing = CookieSharing(
            config, self.global_profile, self, observe_global=False
        )
        self.refresh_settings()

    def create(self, name, directory):
        fresh = not directory.exists()
        directory.mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile(name, self)
        profile.setPersistentStoragePath(str(directory / "storage"))
        profile.setCachePath(str(directory / "cache"))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
        )
        configure_web_settings(profile.settings())
        self.defaults[name] = profile.httpUserAgent()
        override = self.config.get("user_agent_override", "").strip()
        if override:
            profile.setHttpUserAgent(override)
        self.profiles[name] = profile
        self.downloads.attach(profile, name)
        self.adblock.attach(profile)
        if hasattr(self, "sharing"):
            self.sharing.register(name, profile, directory, fresh)
        self.prepare_inspection(name, profile)
        return profile

    def prepare_inspection(self, name, profile):
        # A profile with no page may not initialize Chromium's network context,
        # so startup loadAllCookies alone is insufficient (notably for global).
        page = QWebEnginePage(profile, self)
        page.loadFinished.connect(lambda ok: profile.cookieStore().loadAllCookies())
        self.bootstrap_pages[name] = page
        page.setUrl(QUrl("about:blank"))
        snapshot = CookieSnapshot(page, self)
        self.snapshots[name] = snapshot
        snapshot.loaded.connect(
            lambda cookies, n=name: self.snapshot_loaded(n, cookies)
        )
        snapshot.failed.connect(logger.warning)

    def snapshot_loaded(self, name, cookies):
        if name == "search" or self.sharing.closed:
            return
        if name == "global":
            present = {cookie_key(cookie) for cookie in cookies}
            for key, previous in list(self.sharing.global_cookies.items()):
                if key not in present:
                    self.sharing.global_removed(previous)
        for cookie in cookies:
            if name == "global":
                self.sharing.global_added(cookie)
            else:
                self.sharing.target_added(name, cookie)
        if name == "global":
            self._confirm_global_snapshot(cookies)

    def _confirm_global_snapshot(self, cookies):
        """Re-inspect until the global store stops changing.

        Qt does not report a cookie *overwrite* through cookieAdded, so a
        snapshot is the only way to see an updated value — and nothing triggers
        another snapshot when the overwrite itself was silent. The global profile
        is the shared baseline, so a stale reading silently leaves every profile
        on the old account. Each result is therefore confirmed by a follow-up
        inspection; the loop stops as soon as two consecutive readings agree, or
        once the bound is reached.
        """
        observed = {
            cookie_key(cookie): fingerprint(cookie)
            for cookie in cookies
            if cookie_key(cookie) is not None
        }
        previous = self.global_fingerprints
        self.global_fingerprints = observed
        if previous is None or previous == observed:
            return  # First reading, or the store has settled.
        if self.snapshot_disagreements >= MAX_SNAPSHOT_DISAGREEMENTS:
            self.snapshot_disagreements = 0
            return
        self.snapshot_disagreements += 1
        snapshot = self.snapshots.get("global")
        if snapshot is not None:
            QTimer.singleShot(SNAPSHOT_SETTLE_MS, snapshot.refresh)

    def get(self, name):
        if name == "search":
            return self.search_profile
        if name not in self.profiles:
            self.create(name, get_profile_path(self.config.profiles_dir, name))
        return self.profiles[name]

    def refresh_settings(self):
        override = self.config.get("user_agent_override", "").strip()
        for name, profile in {**self.profiles, "search": self.search_profile}.items():
            profile.setHttpUserAgent(override or self.defaults[name])
        self.adblock.refresh_settings()
        if self.sharing_applies_to_all():
            for name in self.sharing.profiles:
                self.sharing.enable(name)
        else:
            self.sharing.enabled.clear()

    def get_history(self, name):
        if name not in self.histories:
            if name == "search":
                path = None
            elif name == "global":
                path = self.config.config_dir / "global_profile" / "history.json"
            else:
                path = self.config.profiles_dir / name / "history.json"
            self.histories[name] = BrowsingHistory(path, self)
        return self.histories[name]

    def sharing_applies_to_all(self):
        """Whether every profile shares global cookies right now.

        The startup flag governs reconciliation performed while a profile is
        being opened, so it must gate all-profiles mode as well; otherwise the
        checkbox appears to work but inheritance is applied anyway.
        """
        return self.config.get(
            "global_cookie_sharing_mode", "on_switch"
        ) == "all_profiles" and self.config.get(
            "global_cookie_sharing_apply_on_startup", True
        )

    def release(self, owner):
        self.owners.discard(owner)
        if self.owners:
            return
        self.sharing.closed = True
        self.passwords.close()
        for history in self.histories.values():
            history.close()
        self.histories.clear()
        self.downloads.cancel_active()
        for snapshot in self.snapshots.values():
            snapshot.close()
            sip.delete(snapshot.frontend)
        self.snapshots.clear()
        for page in self.bootstrap_pages.values():
            sip.delete(page)
        self.bootstrap_pages.clear()
        for profile in (*self.profiles.values(), self.search_profile):
            sip.delete(profile)
        self.profiles.clear()
        app = QApplication.instance()
        app._browser_profile_registries.pop(str(self.config.config_dir.resolve()), None)
        self.deleteLater()


class ProfileManager(QObject):
    profile_changed = pyqtSignal(str)
    profiles_updated = pyqtSignal()

    def __init__(self, config: Config, initial_profile=None, parent=None):
        super().__init__(parent)
        app = QApplication.instance()
        if not hasattr(app, "_browser_profile_registries"):
            app._browser_profile_registries = {}
        key = str(config.config_dir.resolve())
        registry = app._browser_profile_registries.get(key)
        if registry is None:
            registry = ProfileRegistry(config, app)
            app._browser_profile_registries[key] = registry
        self.registry = registry
        self.config = registry.config
        registry.owners.add(self)
        self._profile_change_callback = self.profiles_updated.emit
        registry.profiles_updated.connect(self._profile_change_callback)
        self.global_profile = registry.global_profile
        self.search_profile = registry.search_profile
        self._profiles = registry.profiles
        self._closed = False
        self.current_profile_name = None
        self.current_profile = None
        self.switch_to_profile(initial_profile or config.default_profile, startup=True)

    def get_profile_path(self, name):
        name = sanitize_profile_name(name)
        if name == "search":
            return Path()
        if name == "global":
            return self.config.config_dir / "global_profile"
        return get_profile_path(self.config.profiles_dir, name)

    def get_profile(self, name):
        return self.registry.get(sanitize_profile_name(name))

    def create_profile(self, name):
        name = sanitize_profile_name(name)
        if (
            not name
            or name in ("global", "search")
            or self.get_profile_path(name).exists()
        ):
            return False
        self.get_profile(name)
        if self.registry.sharing_applies_to_all():
            self.registry.sharing.enable(name)
        self.registry.profiles_updated.emit()
        return True

    def switch_to_profile(self, name, startup=False):
        name = sanitize_profile_name(name)
        self.current_profile_name = name
        self.current_profile = self.get_profile(name)
        apply_on_startup = self.config.get(
            "global_cookie_sharing_apply_on_startup", True
        )
        if name not in ("global", "search") and (not startup or apply_on_startup):
            if self.registry.sharing_applies_to_all():
                self.propagate_global_cookies_to_all_profiles()
            else:
                sharing = self.registry.sharing
                sharing.enabled = {
                    o.current_profile_name
                    for o in self.registry.owners
                    if o.current_profile_name in sharing.profiles
                }
                sharing.enable(name)
        self.profile_changed.emit(name)

    def list_profiles(self):
        names = {"default", "global"}
        names.update(
            p.name
            for p in self.config.profiles_dir.iterdir()
            if p.is_dir() and p.name not in ("search", "global_profile")
        )
        return sorted(names)

    def _available_for_mutation(self, name):
        if name in ("global", "search", "default"):
            return False
        if any(o.current_profile_name == name for o in self.registry.owners):
            return False
        if self.registry.passwords.busy(name):
            return False
        profile = self._profiles.get(name)
        if profile is not None:
            if any(
                isinstance(w, QWebEngineView) and w.page().profile() is profile
                for w in QApplication.allWidgets()
            ):
                return False
            snapshot = self.registry.snapshots.pop(name, None)
            if snapshot is not None:
                snapshot.close()
                sip.delete(snapshot.frontend)
            page = self.registry.bootstrap_pages.pop(name, None)
            if page is not None:
                sip.delete(page)
            sip.delete(profile)
            del self._profiles[name]
            self.registry.sharing.profiles.pop(name, None)
            self.registry.sharing.enabled.discard(name)
        history = self.registry.histories.pop(name, None)
        if history is not None:
            history.close()
            history.deleteLater()
        return True

    def delete_profile(self, name):
        name = sanitize_profile_name(name)
        if not self._available_for_mutation(name):
            return False
        directory = self.get_profile_path(name)
        if not directory.exists():
            return False
        trash = self.config.config_dir / "deleted_profiles"
        trash.mkdir(exist_ok=True)
        try:
            directory.rename(trash / f"{name}-{uuid4().hex}")
        except OSError:
            logger.exception("Could not archive profile %s", name)
            return False
        self.registry.profiles_updated.emit()
        return True

    def rename_profile(self, old_name, new_name):
        old, new = sanitize_profile_name(old_name), sanitize_profile_name(new_name)
        if (
            not new
            or new in ("global", "search", "default")
            or self.get_profile_path(new).exists()
        ):
            return False
        if not self._available_for_mutation(old):
            return False
        try:
            self.get_profile_path(old).rename(self.get_profile_path(new))
        except OSError:
            logger.exception("Could not rename profile %s", old)
            return False
        self.registry.profiles_updated.emit()
        return True

    def is_search_profile(self, name=None):
        return (name or self.current_profile_name) == "search"

    def refresh_settings(self):
        self.registry.refresh_settings()
        if not self.config.get("global_cookie_sharing_apply_on_startup", True):
            # Automatic reconciliation is switched off. Sharing stays inactive
            # until the user switches profile or opts a site back in.
            return
        if self.registry.sharing_applies_to_all():
            self.propagate_global_cookies_to_all_profiles()
        for owner in self.registry.owners:
            if owner.current_profile_name in self.registry.sharing.profiles:
                self.registry.sharing.enable(owner.current_profile_name)

    def protect_cookie_deletion(self, cookies=None):
        self.registry.sharing.protect_deletion(self.current_profile_name, cookies)

    def use_global_cookies(self, domain):
        if self.current_profile_name in self.registry.sharing.profiles:
            self.registry.sharing.use_global(self.current_profile_name, domain)

    def propagate_global_cookies_to_all_profiles(self):
        for name in self.list_profiles():
            if name not in ("global", "search"):
                self.get_profile(name)
                self.registry.sharing.enable(name)

    def close(self):
        if not self._closed:
            self._closed = True
            self.registry.profiles_updated.disconnect(self._profile_change_callback)
            self.registry.release(self)
            self.current_profile = None
