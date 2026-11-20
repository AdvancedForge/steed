"""Cookie management for profiles."""

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QNetworkCookie

from browser import webengine
from browser.cookie_sharing import delete_exact


class CookieInfo:
    """Information about a cookie."""

    def __init__(self, cookie: QNetworkCookie) -> None:
        self.cookie = cookie
        self.name = cookie.name().data().decode("utf-8", errors="ignore")
        self.value = cookie.value().data().decode("utf-8", errors="ignore")
        self.domain = cookie.domain()
        self.path = cookie.path()
        self.expiration_date = cookie.expirationDate()
        self.is_secure = cookie.isSecure()
        self.is_http_only = cookie.isHttpOnly()


class CookieManager(QObject):
    """Manages cookies for a profile."""

    cookies_loaded = pyqtSignal(list)  # Emitted when cookies are loaded

    def __init__(
        self, profile: webengine.Profile, parent: QObject | None = None, snapshot=None
    ) -> None:
        super().__init__(parent)
        self.profile = profile
        self.cookie_store = webengine.cookie_store(profile)
        self.cookies: list[CookieInfo] = []
        self.snapshot = snapshot
        self.status = "Loading stored cookies..." if snapshot else "Live cookie updates"
        self._closed = False
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(
            lambda: self.cookies_loaded.emit(self.get_all_cookies())
        )

        # Connect to cookie store signals
        self.cookie_store.cookieAdded.connect(self._on_cookie_added)
        self.cookie_store.cookieRemoved.connect(self._on_cookie_removed)
        if snapshot is not None:
            snapshot.loaded.connect(self._on_snapshot)
            snapshot.failed.connect(self._on_snapshot_failed)

    def _on_snapshot(self, cookies):
        self.cookies = [CookieInfo(cookie) for cookie in cookies]
        omitted = self.snapshot.skipped_partitioned
        self.status = "Stored and live cookies loaded" + (
            f"; {omitted} partitioned cookies omitted (unsupported)" if omitted else ""
        )
        self._update_timer.start(0)

    def _on_snapshot_failed(self, message):
        self.status = message
        self._update_timer.start(0)

    def close(self) -> None:
        """Detach from the store when switching profiles or closing a window."""
        if self._closed:
            return
        self._closed = True
        self._update_timer.stop()
        self.cookie_store.cookieAdded.disconnect(self._on_cookie_added)
        self.cookie_store.cookieRemoved.disconnect(self._on_cookie_removed)
        if self.snapshot is not None:
            self.snapshot.loaded.disconnect(self._on_snapshot)
            self.snapshot.failed.disconnect(self._on_snapshot_failed)
        self.deleteLater()

    def _on_cookie_added(self, cookie: QNetworkCookie) -> None:
        """Handle when a cookie is added."""
        if self.snapshot is not None:
            # Native signals omit partition identity; use confirmed data. The
            # snapshot watches the same store and schedules its own refresh,
            # honouring the memory mode's wake-up interval.
            return
        cookie_info = CookieInfo(cookie)
        # Update or add to list
        existing = next(
            (
                c
                for c in self.cookies
                if c.name == cookie_info.name
                and c.domain == cookie_info.domain
                and c.path == cookie_info.path
            ),
            None,
        )
        if existing:
            self.cookies.remove(existing)
        self.cookies.append(cookie_info)
        self._update_timer.start(0)

    def _on_cookie_removed(self, cookie: QNetworkCookie) -> None:
        """Handle when a cookie is removed."""
        if self.snapshot is not None:
            return  # The snapshot schedules its own refresh, as above.
        cookie_info = CookieInfo(cookie)
        self.cookies = [
            c
            for c in self.cookies
            if not (
                c.name == cookie_info.name
                and c.domain == cookie_info.domain
                and c.path == cookie_info.path
            )
        ]
        self._update_timer.start(0)

    def load_all_cookies(self) -> None:
        """Load all cookies from the store."""
        self.cookie_store.loadAllCookies()
        if self.snapshot is not None:
            self.snapshot.refresh()

    def get_cookies_for_domain(self, domain: str) -> list[CookieInfo]:
        """Get cookies for a specific domain."""
        domain = domain.lower().lstrip(".")
        return [
            c
            for c in self.cookies
            if c.domain.lower().lstrip(".") == domain
            or c.domain.lower().lstrip(".").endswith("." + domain)
        ]

    def delete_cookie(self, cookie_info: CookieInfo) -> None:
        """Delete a specific cookie."""
        delete_exact(self.cookie_store, cookie_info.cookie)

    def delete_cookies_for_domain(self, domain: str) -> None:
        """Delete all cookies for a domain."""
        cookies_to_delete = self.get_cookies_for_domain(domain)
        for cookie_info in cookies_to_delete:
            self.delete_cookie(cookie_info)

    def clear_all_cookies(self) -> None:
        """Clear all cookies."""
        self.cookie_store.deleteAllCookies()

    def get_all_cookies(self) -> list[CookieInfo]:
        """Get all cookies."""
        return self.cookies.copy()
