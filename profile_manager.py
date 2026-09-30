"""Profile management for the browser."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
import shutil

from PyQt6.QtCore import QObject, pyqtSignal, QEventLoop, QTimer, QUrl
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings, QWebEngineScript

from config import Config
from utils import sanitize_profile_name, get_profile_path, load_json, save_json

class ProfileManager(QObject):
    """Manages browser profiles. Each profile is a real persistent QWebEngineProfile.
    'global' is a persistent shared-cookie profile. 'search' is off-the-record.
    """

    profile_changed = pyqtSignal(str)
    profiles_updated = pyqtSignal()

    def __init__(self, config: Config, initial_profile: Optional[str] = None):
        super().__init__()
        self.config = config

        self.current_profile_name: Optional[str] = None
        self.current_profile: Optional[QWebEngineProfile] = None

        self._profiles: Dict[str, QWebEngineProfile] = {}

        # Global profile (persistent)
        self.global_profile = self._create_persistent_profile(
            "global",
            self.config.config_dir / "global_profile",
        )

        self._global_cookie_sharing_mode = self.config.get("global_cookie_sharing_mode", "on_switch")
        self._global_cookie_apply_on_startup = self.config.get("global_cookie_sharing_apply_on_startup", True)

        self._global_active_host: str = ""
        # ---- Global cookie cache (for cross-profile sharing) ----
        self._global_cookie_map: dict[tuple[bytes, str, str], QNetworkCookie] = {}

        self._global_cookie_index_file = (self.config.config_dir / "global_profile" / "cookie_host_index.json")
        self._global_cookie_host_index = load_json(self._global_cookie_index_file, default={})

        gstore = self.global_profile.cookieStore()
        gstore.cookieAdded.connect(self._on_global_cookie_added)
        gstore.cookieRemoved.connect(self._on_global_cookie_removed)

        # Try to populate cache from disk on startup (best effort)
        gstore.loadAllCookies()


        # Search/incognito profile (off-the-record)
        self.search_profile = QWebEngineProfile()  # off-the-record by default (no name)
        self.search_profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.search_profile.setHttpCacheMaximumSize(0)

        self.switch_to_profile(initial_profile or self.config.default_profile)

    # ---------- Paths ----------
    def get_profile_path(self, profile_name: str) -> Path:
        name = sanitize_profile_name(profile_name)
        if name in ("search",):
            return Path()
        if name == "global":
            return self.config.config_dir / "global_profile"
        return get_profile_path(self.config.profiles_dir, name)

    def get_profile_data_path(self, profile_name: str) -> Path:
        """Where tab/session files go for this profile."""
        return self.get_profile_path(profile_name)

    # ---------- QWebEngineProfile creation ----------
    def _create_persistent_profile(self, name: str, base_dir: Path) -> QWebEngineProfile:
        base_dir.mkdir(parents=True, exist_ok=True)
        (base_dir / "storage").mkdir(parents=True, exist_ok=True)
        (base_dir / "cache").mkdir(parents=True, exist_ok=True)

        profile = QWebEngineProfile(name)
        profile.setPersistentStoragePath(str(base_dir / "storage"))
        profile.setCachePath(str(base_dir / "cache"))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        profile.setHttpUserAgent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

        s = profile.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)
        try:
            s.setAttribute(QWebEngineSettings.WebAttribute.ImageAnimationEnabled, True)
        except Exception:
            pass

        return profile

    def _get_or_create_profile(self, profile_name: str) -> QWebEngineProfile:
        name = sanitize_profile_name(profile_name)

        if name == "search":
            return self.search_profile
        if name == "global":
            return self.global_profile

        if name not in self._profiles:
            base_dir = self.get_profile_path(name)
            base_dir.mkdir(parents=True, exist_ok=True)
            self._profiles[name] = self._create_persistent_profile(name, base_dir)

        return self._profiles[name]

    # ---------- Public API ----------
    def create_profile(self, profile_name: str) -> bool:
        name = sanitize_profile_name(profile_name)
        if not name or name in ("search", "global"):
            return False
        path = self.get_profile_path(name)
        path.mkdir(parents=True, exist_ok=True)
        # Ensure engine profile exists too
        _ = self._get_or_create_profile(name)
        self.profiles_updated.emit()
        return True

    def get_profile(self, profile_name: str) -> QWebEngineProfile:
        return self._get_or_create_profile(profile_name)

    def switch_to_profile(self, profile_name: str):
        name = sanitize_profile_name(profile_name)
        self.current_profile_name = name
        self.current_profile = self.get_profile(name)

        if name == "global":
            self.global_profile.cookieStore().loadAllCookies()  # :contentReference[oaicite:6]{index=6}

        if name not in ("search", "global"):
            if self._global_cookie_sharing_mode == "all_profiles":
                QTimer.singleShot(0, self.propagate_global_cookies_to_all_profiles)
            else:
                target = self.current_profile
                self.global_profile.cookieStore().loadAllCookies()
                QTimer.singleShot(250, lambda p=target: self._apply_global_cookies_to(p))

        self.profile_changed.emit(name)



    def list_profiles(self) -> List[str]:
        profiles: List[str] = ["global"]

        # Ensure default exists
        default_path = self.get_profile_path("default")
        default_path.mkdir(parents=True, exist_ok=True)

        if self.config.profiles_dir.exists():
            for p in self.config.profiles_dir.iterdir():
                if p.is_dir():
                    profiles.append(p.name)

        # Don’t show global folder if it exists somewhere else
        profiles = [p for p in profiles if p != "global_profile"]
        # Always allow search in UI (BrowserWindow appends it)
        return sorted(set(profiles))

    def delete_profile(self, profile_name: str) -> bool:
        name = sanitize_profile_name(profile_name)
        if name in ("search", "default", "global"):
            return False

        path = self.get_profile_path(name)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

        # Remove engine profile instance
        prof = self._profiles.pop(name, None)
        if prof is not None:
            prof.deleteLater()

        self.profiles_updated.emit()
        return True

    def rename_profile(self, old_name: str, new_name: str) -> bool:
        old = sanitize_profile_name(old_name)
        new = sanitize_profile_name(new_name)

        if old in ("search", "default", "global") or new in ("search", "global") or not new:
            return False

        old_path = self.get_profile_path(old)
        new_path = self.get_profile_path(new)

        if not old_path.exists() or new_path.exists():
            return False

        shutil.copytree(old_path, new_path)
        shutil.rmtree(old_path, ignore_errors=True)

        # Update engine profile cache
        old_prof = self._profiles.pop(old, None)
        if old_prof is not None:
            old_prof.deleteLater()
        self._profiles.pop(new, None)  # ensure clean
        _ = self._get_or_create_profile(new)

        if self.current_profile_name == old:
            self.current_profile_name = new
            self.current_profile = self.get_profile(new)

        self.profile_changed.emit(self.current_profile_name)
        self.profiles_updated.emit()
        return True

    def is_search_profile(self, name: Optional[str] = None) -> bool:
        return (name or self.current_profile_name) == "search"

    def is_global_profile(self, name: Optional[str] = None) -> bool:
        return (name or self.current_profile_name) == "global"

    # ---------- Cookie persistence helpers ----------
    def flush_all_cookie_stores(self):
        """Force cookie flush for all persistent profiles."""
        for prof in list(self._profiles.values()) + [self.global_profile]:
            try:
                prof.cookieStore().flushStore()
            except Exception:
                pass

    def _sync_global_cookies_to(self, target_profile: QWebEngineProfile, timeout_ms: int = 800):
        """Import cookies from global profile into another persistent profile."""
        src_store = self.global_profile.cookieStore()
        dst_store = target_profile.cookieStore()

        collected: List[QNetworkCookie] = []

        def on_added(cookie: QNetworkCookie):
            collected.append(cookie)

        src_store.cookieAdded.connect(on_added)
        src_store.loadAllCookies()

        loop = QEventLoop()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()

        try:
            src_store.cookieAdded.disconnect(on_added)
        except Exception:
            pass

        for c in collected:
            domain = c.domain().lstrip(".")
            if not domain:
                continue
            scheme = "https" if c.isSecure() else "http"
            origin = QUrl(f"{scheme}://{domain}")
            dst_store.setCookie(c, origin)

        # Best-effort flush to disk
        try:
            dst_store.flushStore()
        except Exception:
            pass
    
    def _cookie_key(self, cookie: QNetworkCookie):
        name_b = cookie.name().data()
        domain = (cookie.domain() or "").strip()
        path = cookie.path() or "/"

        if not domain:
            # Try persisted mapping first, then active host (during browsing)
            host = self._global_cookie_host_index.get(self._host_index_key(cookie), "") or self._global_active_host
            domain = host

        return (name_b, domain, path)

    def _on_global_cookie_added(self, cookie: QNetworkCookie):
        key = self._cookie_key(cookie)
        if not key[1]:
            return

        # If host-only, persist host hint for next restart
        if not (cookie.domain() or "").strip() and self._global_active_host:
            self._global_cookie_host_index[self._host_index_key(cookie)] = self._global_active_host
            save_json(self._global_cookie_index_file, self._global_cookie_host_index)

        self._global_cookie_map[key] = QNetworkCookie(cookie)

        # If mode is all_profiles, push this cookie out immediately
        if self._global_cookie_sharing_mode == "all_profiles":
            self._apply_single_global_cookie_to_all_profiles(QNetworkCookie(cookie), key[1])

    def _on_global_cookie_removed(self, cookie: QNetworkCookie):
        key = self._cookie_key(cookie)
        if key[1]:
            self._global_cookie_map.pop(key, None)

    def _apply_global_cookies_to(self, target_profile: QWebEngineProfile):
        if target_profile is None:
            return
        dst = target_profile.cookieStore()

        for (name_b, dom_or_host, path), cookie in list(self._global_cookie_map.items()):
            host = dom_or_host.lstrip(".").strip()
            if not host:
                continue

            scheme = "https" if cookie.isSecure() else "http"
            origin = QUrl(f"{scheme}://{host}/")
            dst.setCookie(cookie, origin)

        try:
            dst.flushStore()
        except Exception:
            pass


    def note_global_active_url(self, url: QUrl):
        host = url.host().strip()
        if host:
            self._global_active_host = host

    def _host_index_key(self, cookie: QNetworkCookie) -> str:
        # Use name bytes to avoid unicode issues; include path + secure
        name_hex = cookie.name().data().hex()
        path = cookie.path() or "/"
        sec = "S" if cookie.isSecure() else "N"
        return f"{name_hex}|{path}|{sec}"

    def propagate_global_cookies_to_all_profiles(self):
        # Ensure global cookies are loaded (async); this helps refresh _global_cookie_map
        self.global_profile.cookieStore().loadAllCookies()  # :contentReference[oaicite:5]{index=5}

        for name in self.list_profiles():
            if name in ("global", "search"):
                continue
            prof = self.get_profile(name)
            self._apply_global_cookies_to(prof)
