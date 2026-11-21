"""Window-local selection backed by an application-owned engine registry."""

import logging
import os
from pathlib import Path
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWidgets import QApplication

from browser import webengine
from browser.ad_blocker import AdBlocker
from browser.browsing_history import BrowsingHistory
from browser.config import Config
from browser.cookie_sharing import CookieSharing, cookie_key, fingerprint
from browser.cookie_snapshot import CookieSnapshot
from browser.download_manager import DownloadManager
from browser.password_service import PasswordService
from browser.tab_manager import ClosedTabHistory
from browser.utils import get_profile_path, sanitize_profile_name
from browser.webengine import browser_user_agent, configure_web_settings
from services.command_broker import require_owner_thread
from services.navigation_service import NavigationService
from services.profile_storage import ProfileStorage
from services.site_permissions import SitePermissions, configure_permission_policy
from services.start_pages import StartPageHandler, StartPages
from services.task_policy import task_profile_name
from ui.download_dialog import choose_download_destination

logger = logging.getLogger(__name__)

# How many follow-up inspections confirm a still-changing global store before
# the reading is accepted as settled.
MAX_SNAPSHOT_DISAGREEMENTS = 3

# Delay before the confirming inspection, so a burst of writes can finish first.
SNAPSHOT_SETTLE_MS = 250


class ProfileRegistry(QObject):
    """One engine profile per storage path; last window flushes engine storage."""

    profiles_updated = pyqtSignal()
    agent_access_changed = pyqtSignal(bool)
    layout_changed = pyqtSignal(object)
    settings_changed = pyqtSignal()
    profile_usage_changed = pyqtSignal()
    site_settings_changed = pyqtSignal(str, str)  # profile, host

    def __init__(self, config, parent):
        super().__init__(parent)
        self.config = config
        self.storage = ProfileStorage(config)
        self.profiles = {}
        self.bootstrap_pages = {}
        self.snapshots = {}
        self.defaults = {}
        self.histories = {}
        self.closed_tabs = ClosedTabHistory()
        self.start_pages = StartPages(self)
        from services.page_style import PageStyle

        self.page_style = PageStyle(self)
        self.snapshot_disagreements = 0
        self.global_fingerprints = None
        self.owners = set()
        self.last_active_owner = None
        self._tasks = None
        self._agent_providers = None
        self._app_connections = None
        self._watches = None
        self.site_permissions = SitePermissions(
            config.config_dir / "site_permissions.json"
        )
        self._private_permission_key = uuid4().hex
        self._site_settings = {}
        # Profiles whose cookie inspection waits until it is needed.
        self.deferred_inspection = {}
        self._user_scripts = {}
        self.agent_gateway = None
        self.task_profiles = {}
        self.navigation = NavigationService(self)
        self.downloads = DownloadManager(
            config, self, destination_picker=choose_download_destination
        )
        from services.safe_browsing import SafeBrowsing

        self.safe_browsing = SafeBrowsing(config, self)
        self.downloads.checker = self.safe_browsing.check
        from services import updates

        self.updates = updates.Updates(config, self, project=updates.PROJECT)
        from browser import download_manager

        self.downloads.confirm_program = download_manager.confirm_program
        self.passwords = PasswordService(config, self)
        # Bitwarden, once unlocked in Steed (General → Bitwarden…).
        self.bitwarden = None
        from browser.passkeys import Passkeys

        self.passkeys = Passkeys(self)
        self.password_service = self.passwords
        self.adblock = AdBlocker(config, self)
        from browser.extensions import Extensions

        self.extensions = Extensions(self)
        from browser.sync import SyncService

        self.sync = SyncService(self)
        self.global_profile = self.create(
            "global", config.config_dir / "global_profile"
        )
        self._create_search_profile()
        # Confirm source identities through a full snapshot: live Qt signals
        # cannot distinguish partitioned cookies and overwrite notifications.
        self.sharing = CookieSharing(
            config, self.global_profile, self, observe_global=False
        )
        self.refresh_settings()
        self.settings_changed.connect(
            lambda: self._inspect_on_demand() or self.start_deferred_inspection()
        )

    def site_settings(self, name):
        """Zoom, JavaScript, autoplay and pop-ups per site for one profile."""
        from browser.site_settings import SiteSettings

        if name not in self._site_settings:
            path = None
            if name == "global":
                path = self.config.config_dir / "global_profile" / "site_settings.json"
            elif name != "search":
                path = get_profile_path(self.config.profiles_dir, name)
                path = path / "site_settings.json"
            store = SiteSettings(path, self)
            store.changed.connect(
                lambda host, name=name: self.site_settings_changed.emit(name, host)
            )
            self._site_settings[name] = store
        return self._site_settings[name]

    def user_scripts(self, name):
        """Your CSS and JavaScript for chosen sites; None for incognito."""
        from browser.user_scripts import UserScripts

        if name == "search":
            return None
        if name not in self._user_scripts:
            if name == "global":
                folder = self.config.config_dir / "global_profile"
            else:
                folder = get_profile_path(self.config.profiles_dir, name)
            store = UserScripts(folder / "user_scripts.json", self)
            store.changed.connect(lambda name=name: self._user_scripts_changed(name))
            self._user_scripts[name] = store
        return self._user_scripts[name]

    def _user_scripts_changed(self, name):
        profile = self.profiles.get(name)
        if profile is not None:
            self._user_scripts[name].install(profile)

    def _create_search_profile(self):
        """A fresh off-the-record profile for incognito windows."""
        # A new incognito session starts with no remembered site settings.
        old = getattr(self, "_site_settings", {}).pop("search", None)
        if old is not None:
            old.deleteLater()
        profile = webengine.private_profile(self)
        webengine.install_scheme(
            profile, b"steed", StartPageHandler(self.start_pages, "search", profile)
        )
        configure_permission_policy(profile)
        self.defaults["search"] = browser_user_agent(profile.httpUserAgent())
        profile.setHttpUserAgent(
            self.config.get("user_agent_override", "").strip()
            or self.defaults["search"]
        )
        configure_web_settings(profile.settings())
        self.downloads.attach(profile, "search")
        self.adblock.attach(profile)
        self.search_profile = profile
        self._spell_profile(profile)
        self.prepare_inspection("search", profile)

    def reset_search_profile(self):
        """Forget incognito cookies, storage and cache once nothing uses them.

        Like closing the last incognito window in other browsers: the next
        incognito window starts empty. Returns False while anything still
        shows or downloads through the current incognito profile.
        """
        require_owner_thread(self)
        old = self.search_profile
        if any(owner.current_profile_name == "search" for owner in self.owners):
            return False
        if any(
            webengine.is_view(widget)
            and not sip.isdeleted(widget)
            and widget.page() is not None
            and widget.page().profile() is old
            for widget in QApplication.allWidgets()
        ):
            return False
        if any(
            record.profile == "search" and record.state == "DownloadInProgress"
            for record in self.downloads.records.values()
        ):
            return False
        self.deferred_inspection.pop("search", None)
        snapshot = self.snapshots.pop("search", None)
        if snapshot is not None:
            snapshot.dispose()
        page = self.bootstrap_pages.pop("search", None)
        if page is not None:
            sip.delete(page)
        self._create_search_profile()
        for owner in self.owners:
            owner.search_profile = self.search_profile
        # Pages first, then their profile.
        sip.delete(old)
        return True

    def schedule_search_reset(self, attempts=10):
        """Retry shortly: tabs of the incognito window are deleted later."""

        def attempt(remaining):
            if self.sharing.closed or self.reset_search_profile() or remaining <= 1:
                return
            QTimer.singleShot(500, lambda: attempt(remaining - 1))

        QTimer.singleShot(200, lambda: attempt(attempts))

    @property
    def app_connections(self):
        require_owner_thread(self)
        if self._app_connections is None:
            # Agent and app services load on first use, not at startup.
            from services.app_connections import AppConnections

            self._app_connections = AppConnections(
                self.config.config_dir / "app_accounts.json", self
            )
        return self._app_connections

    def commit_layout(self, preferences, *, base=None):
        """Commit presentation choices and notify every window sharing storage."""
        from browser.layout_preferences import (
            merge_preferences,
            preferences_from_config,
        )

        require_owner_thread(self)
        if base is not None:
            preferences = merge_preferences(
                base, preferences, preferences_from_config(self.config)
            )
        from browser.layout_preferences import CONFIG_KEY

        previous = preferences_from_config(self.config)
        values = {CONFIG_KEY: preferences.to_dict()}
        if previous.to_dict()["layout"] != preferences.to_dict()["layout"]:
            values["layout_previous"] = previous.to_dict()
        self.config.update(values)
        self.layout_changed.emit(preferences.copy())
        return preferences.copy()

    @property
    def tasks(self):
        """Initialize task infrastructure only when an internal client uses it."""
        require_owner_thread(self)
        if self._tasks is None:
            from services.task_service import TaskService
            from ui.task_approval import TaskApprovalQueue

            self._tasks = TaskService(
                self.config.config_dir / "tasks.sqlite",
                self,
                profiles=self,
                connectors=self.app_connections.connectors,
                connections=self.app_connections,
            )
            self.app_connections.task_service = self._tasks
            self.task_approvals = TaskApprovalQueue(self, self._tasks)
            self._tasks.failed.connect(lambda _: self.set_agent_access(False))
            for owner in self.owners:
                window = owner.parent()
                if window is not None and hasattr(window, "auto_approval_action"):
                    self._tasks.changed.connect(window._refresh_auto_approval)
                    window._refresh_auto_approval()
        return self._tasks

    @property
    def spellcheck(self):
        """Spell check dictionaries and switching (services.spellcheck)."""
        if getattr(self, "_spellcheck", None) is None:
            from services.spellcheck import Spellcheck

            self._spellcheck = Spellcheck(self)
            self._spellcheck.ready.connect(lambda _: self.apply_spellcheck())
        return self._spellcheck

    def apply_spellcheck(self):
        """Every profile, as Settings → General → Spell check says; a missing
        dictionary is downloaded first (then applied)."""
        from services.spellcheck import installed

        language = self.config.get("spellcheck_language", "") or ""
        profiles = [*self.profiles.values()]
        if getattr(self, "search_profile", None) is not None:
            profiles.append(self.search_profile)
        self.spellcheck.apply(profiles, language)
        if (
            language
            and not installed(language, self.spellcheck.where)
            and self.spellcheck.wanted != language
        ):
            self.spellcheck.fetch(language)

    @property
    def routines(self):
        """Agent tasks on a schedule (services.routines)."""
        if getattr(self, "_routines", None) is None:
            from services.routines import Routines

            self._routines = Routines(self)
        return self._routines

    @property
    def automations(self):
        """Your workflows: a trigger and steps (services.automations)."""
        if getattr(self, "_automations", None) is None:
            from services.automations import Automations

            self._automations = Automations(self)
            self.watches.fired.connect(self._automations.watch_changed)
            automations = self._automations
            self.downloads.finished.connect(
                lambda key: (
                    key in self.downloads.records
                    and automations.download_finished(self.downloads.records[key])
                )
            )
        return self._automations

    @property
    def skills(self):
        """Things you ask your assistant often (services.skills)."""
        if getattr(self, "_skills", None) is None:
            from services.skills import Skills

            self._skills = Skills(self.config.config_dir, self)
        return self._skills

    @property
    def watches(self):
        """Pages Steed keeps an eye on for you (services.watches)."""
        if self._watches is None:
            from services.watches import WatchService

            self._watches = WatchService(self)
        return self._watches

    @property
    def agent_providers(self):
        require_owner_thread(self)
        if self._agent_providers is None:
            from services.agent_providers import AgentProviders

            self._agent_providers = AgentProviders(self, self)
        return self._agent_providers

    def agent_context(self):
        """Report selected profile metadata, never page URLs or browsing state."""
        require_owner_thread(self)
        windows = {
            owner.parent(): owner
            for owner in self.owners
            if owner.parent() is not None
            and hasattr(owner.parent(), "browser_window_id")
            and not getattr(owner.parent(), "_closing", False)
        }
        active = QApplication.activeWindow()
        while active is not None and active not in windows:
            active = active.parentWidget()
        owner, source = windows.get(active), "active_window"
        if owner is None and self.last_active_owner in windows.values():
            owner, source = self.last_active_owner, "last_active_window"
        if owner is None and len(windows) == 1:
            owner, source = next(iter(windows.values())), "only_window"
        if owner is None:
            return {
                "window_id": None,
                "profile": None,
                "profile_selector": None,
                "shared_profile_available": False,
                "source": "no_active_window",
            }
        name = owner.current_profile_name
        shared = name not in (None, "search")
        return {
            "window_id": owner.parent().browser_window_id,
            "profile": name,
            "profile_selector": "existing:" + name if shared else None,
            "shared_profile_available": shared,
            "source": source,
        }

    def set_agent_access(self, enabled):
        """Opt in for this browser run; each connection still needs user grants."""
        require_owner_thread(self)
        if enabled == (self.agent_gateway is not None):
            return
        if enabled:
            from services.agent_gateway import AgentGateway

            self.agent_gateway = AgentGateway(self.tasks, self.config.config_dir, self)
        else:
            if self._agent_providers is not None:
                self._agent_providers.close()
            gateway, self.agent_gateway = self.agent_gateway, None
            gateway.close()
            gateway.deleteLater()
        self.agent_access_changed.emit(bool(enabled))

    def validate_task_profile(self, selector):
        name = task_profile_name(selector)
        directory = (
            self.config.config_dir / "global_profile"
            if name == "global"
            else self.config.profiles_dir / name
        )
        root = self.config.config_dir if name == "global" else self.config.profiles_dir
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or directory.resolve().parent != root.resolve()
        ):
            raise ValueError("The requested existing profile is unavailable")
        return name

    def approval_profile_key(self, selector, *, create=False):
        """Identity survives renames, but a newly created profile gets fresh authority."""
        require_owner_thread(self)
        name = self.validate_task_profile(selector)
        directory = (
            self.config.config_dir / "global_profile"
            if name == "global"
            else self.config.profiles_dir / name
        )
        return self._permission_identity(directory, create=create)

    @staticmethod
    def _permission_identity(directory, *, create=False):
        path = directory / ".steed_permission_id"
        if directory.is_symlink():
            raise ValueError("Invalid profile permission directory")
        if path.is_symlink():
            raise ValueError("Invalid profile permission identity")
        try:
            with path.open("rb") as handle:
                key = handle.read(33).decode("ascii")
        except FileNotFoundError:
            if not create:
                return ""
            key = uuid4().hex
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
        if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid profile permission identity")
        return key

    def site_permission_context(self, profile):
        """Resolve the requesting page's profile, never the foreground selection."""
        require_owner_thread(self)
        if profile is self.search_profile:
            return {
                "key": self._private_permission_key,
                "profile": "Search (private)",
                "private": True,
            }
        for name, candidate in self.profiles.items():
            if candidate is profile:
                return {
                    "key": self._permission_identity(
                        self.config.config_dir / "global_profile", create=True
                    )
                    if name == "global"
                    else self.approval_profile_key("existing:" + name, create=True),
                    "profile": name,
                    "private": False,
                }
        raise ValueError("Website permission profile is unavailable")

    def reserve_task_profile(self, task_id, selector):
        """Reserve on the Qt thread, without granting engine authority."""
        require_owner_thread(self)
        name = self.validate_task_profile(selector)
        self.get(name)
        self.task_profiles.setdefault(name, set()).add(task_id)
        self.profile_usage_changed.emit()

    def task_profile(self, task_id, selector):
        require_owner_thread(self)
        name = task_profile_name(selector)
        if task_id not in self.task_profiles.get(name, set()):
            raise ValueError("The task does not hold this profile")
        return self.profiles[name]

    def release_task_profiles(self, task_id=None):
        require_owner_thread(self)
        previous = set(self.task_profiles)
        for name, tasks in list(self.task_profiles.items()):
            if task_id is None:
                tasks.clear()
            else:
                tasks.discard(task_id)
            if not tasks:
                self.task_profiles.pop(name)
        if previous != set(self.task_profiles):
            self.profile_usage_changed.emit()

    def check_user_profile(self, name):
        # Task authority belongs to its pages; user browsing remains available.
        require_owner_thread(self)

    def activate_user_profile(self, name):
        self.check_user_profile(name)
        return self.get(name)

    def create(self, name, directory, *, new_profile=False):
        if (
            not new_profile
            and name in self.storage.retired
            and (not directory.exists() or self.storage.retired_stub(name, directory))
        ):
            raise ValueError(
                "This profile was renamed or archived. Create it explicitly to reuse its name."
            )
        fresh = new_profile or not directory.exists()
        storage, cache = self.storage.prepare(name, directory, new=fresh)
        profile = webengine.stored_profile(
            name,
            self,
            storage=storage,
            cache=cache,
            keep_session_cookies=self._keep_session_cookies(),
        )
        configure_permission_policy(profile)
        configure_web_settings(profile.settings())
        webengine.install_scheme(
            profile, b"steed", StartPageHandler(self.start_pages, name, profile)
        )
        self.defaults[name] = browser_user_agent(profile.httpUserAgent())
        profile.setHttpUserAgent(self.defaults[name])
        override = self.config.get("user_agent_override", "").strip()
        if override:
            profile.setHttpUserAgent(override)
        self.profiles[name] = profile
        self._spell_profile(profile)
        self.passwords.install(profile)
        self.passkeys.install(profile)
        from services import web_speech

        webengine.add_profile_script(
            profile,
            "steed-speech",
            web_speech.shim(),
            world=webengine.PAGE_WORLD,
            when="start",
            frames=True,
        )
        from services import webmcp

        # Tools sites offer to agents (WebMCP), as Chrome is adding.
        webengine.add_profile_script(
            profile,
            "steed-webmcp",
            webmcp.shim(),
            world=webengine.PAGE_WORLD,
            when="start",
        )
        from browser import privacy

        privacy.install_fingerprint_guard(
            profile, bool(self.config.get("resist_fingerprinting", False))
        )
        self.user_scripts(name).install(profile)
        self.downloads.attach(profile, name)
        self.adblock.attach(profile)
        self.extensions.attach(name, profile, directory)
        if hasattr(self, "sharing"):
            self.sharing.register(name, profile, directory, fresh)
        self.prepare_inspection(name, profile)
        return profile

    def _inspect_on_demand(self):
        from browser import memory_modes

        return memory_modes.values(self.config)["cookie_inspection"] == "needed"

    def prepare_inspection(self, name, profile):
        if self._inspect_on_demand():
            self.deferred_inspection[name] = profile
            return
        self._start_inspection(name, profile)

    def ensure_inspection(self, name):
        """Start inspection a shown profile needs: it and the global profile.

        Only a normal profile receives shared cookies, so the global profile
        alone, and incognito, need none.
        """
        if name in ("global", "search"):
            return
        for needed in (name, "global"):
            profile = self.deferred_inspection.pop(needed, None)
            if profile is not None and self.profiles.get(needed) is profile:
                self._start_inspection(needed, profile)

    def start_deferred_inspection(self):
        """After switching the setting back to "Start with Steed"."""
        for name, profile in list(self.deferred_inspection.items()):
            del self.deferred_inspection[name]
            if self.profiles.get(name) is profile or (
                name == "search" and profile is self.search_profile
            ):
                self._start_inspection(name, profile)

    def _start_inspection(self, name, profile):
        # A profile with no page may not initialize Chromium's network context,
        # so startup loadAllCookies alone is insufficient (notably for global).
        page = webengine.Page(profile, self)
        page.loadFinished.connect(
            lambda ok: webengine.cookie_store(profile).loadAllCookies()
        )
        self.bootstrap_pages[name] = page
        page.setUrl(QUrl("about:blank"))
        snapshot = CookieSnapshot(page, self)
        self.snapshots[name] = snapshot
        snapshot.loaded.connect(
            lambda cookies, n=name, source=profile: (
                self.snapshot_loaded(n, cookies)
                if self.profiles.get(n) is source
                else None
            )
        )
        snapshot.failed.connect(logger.warning)

    def snapshot_loaded(self, name, cookies):
        if name == "search" or self.sharing.closed or name not in self.profiles:
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

    def _spell_profile(self, profile):
        from services.spellcheck import folder, installed

        language = self.config.get("spellcheck_language", "") or ""
        if language and installed(language, folder()):
            profile.setSpellCheckEnabled(True)
            profile.setSpellCheckLanguages([language])

    def get(self, name):
        if name == "search":
            return self.search_profile
        if name not in self.profiles:
            self.create(name, get_profile_path(self.config.profiles_dir, name))
        return self.profiles[name]

    def _keep_session_cookies(self):
        """Keep session cookies when tabs are restored, as Chrome does."""
        return bool(self.config.get("restore_tabs_on_startup", True))

    def refresh_settings(self):
        from browser import privacy

        guard = bool(self.config.get("resist_fingerprinting", False))
        for profile in (*self.profiles.values(), self.search_profile):
            privacy.install_fingerprint_guard(profile, guard)
        override = self.config.get("user_agent_override", "").strip()
        for name, profile in {**self.profiles, "search": self.search_profile}.items():
            profile.setHttpUserAgent(override or self.defaults[name])
        for profile in self.profiles.values():
            # Takes effect for this profile's cookie store at the next start.
            webengine.set_keep_session_cookies(profile, self._keep_session_cookies())
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
        self.profile_usage_changed.emit()
        if self.last_active_owner is owner:
            self.last_active_owner = None
        if self.owners:
            if owner.current_profile_name == "search":
                self.schedule_search_reset()
            return
        self.set_agent_access(False)
        self.sharing.closed = True
        if self._watches is not None:
            self._watches.close()  # Its page goes before the profiles.
        self.safe_browsing.close()
        if getattr(self, "_routines", None) is not None:
            self._routines.close()  # Its page goes before the profiles.
        if getattr(self, "_automations", None) is not None:
            self._automations.close()  # Its page goes before the profiles.
        self.updates.close()
        if self._app_connections is not None:
            self._app_connections.close()
        if self._tasks is not None:
            try:
                self._tasks.close()
            except Exception:
                logger.error("Could not persist task shutdown; sessions were disposed")
        self.passwords.close()
        for history in self.histories.values():
            history.close()
        self.histories.clear()
        self.downloads.cancel_active()
        for snapshot in self.snapshots.values():
            snapshot.dispose()
        self.snapshots.clear()
        for page in self.bootstrap_pages.values():
            sip.delete(page)
        self.bootstrap_pages.clear()
        self.extensions.close()
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
        registry.check_user_profile(initial_profile or config.default_profile)
        registry.owners.add(self)
        if registry._tasks is not None:
            registry.task_approvals.schedule()
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
        self.registry.check_user_profile(name)
        return self.registry.get(sanitize_profile_name(name))

    def create_profile(self, name):
        name = sanitize_profile_name(name)
        if (
            not name
            or name in ("global", "search")
            or name in self._profiles
            or (
                self.get_profile_path(name).exists()
                and not self.registry.storage.retired_stub(
                    name, self.get_profile_path(name)
                )
            )
        ):
            return False
        self.registry.create(name, self.get_profile_path(name), new_profile=True)
        if self.registry.sharing_applies_to_all():
            self.registry.sharing.enable(name)
        self.registry.profiles_updated.emit()
        return True

    def switch_to_profile(self, name, startup=False):
        name = sanitize_profile_name(name)
        leaving_private = self.current_profile_name == "search" and name != "search"
        profile = self.registry.activate_user_profile(name)
        self.current_profile_name = name
        self.current_profile = profile
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
        self.registry.profile_usage_changed.emit()
        if leaving_private:
            self.registry.schedule_search_reset()

    def list_profiles(self):
        names = {"global"}
        names.update(
            p.name
            for p in self.config.profiles_dir.iterdir()
            if p.name != "search" and self.registry.storage.active(p.name, p)
        )
        return sorted(names)

    def _available_for_mutation(self, name):
        if name in self.registry.task_profiles:
            return False
        if name in ("global", "search"):
            return False
        if any(o.current_profile_name == name for o in self.registry.owners):
            return False
        if self.registry.passwords.busy(name):
            return False
        profile = self._profiles.get(name)
        if profile is not None:
            if any(
                webengine.is_view(w) and w.page().profile() is profile
                for w in QApplication.allWidgets()
            ):
                return False
            snapshot = self.registry.snapshots.pop(name, None)
            if snapshot is not None:
                snapshot.dispose()
            page = self.registry.bootstrap_pages.pop(name, None)
            if page is not None:
                sip.delete(page)
            self.registry.extensions.detach(name)
            sip.delete(profile)
            del self._profiles[name]
        self.registry.sharing.unregister(name)
        history = self.registry.histories.pop(name, None)
        self.registry.start_pages.forget(name)
        self.registry.extensions.detach(name)
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
            self.registry.storage.retire(name, directory)
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
            or new in ("global", "search")
            or self.get_profile_path(new).exists()
        ):
            return False
        if not self._available_for_mutation(old):
            return False
        try:
            self.registry.storage.retire(old, self.get_profile_path(old))
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
