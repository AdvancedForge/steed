"""Cookie management for profiles."""

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtWebEngineCore import QWebEngineProfile


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

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "expiration": self.expiration_date.toString()
            if self.expiration_date.isValid()
            else None,
            "secure": self.is_secure,
            "http_only": self.is_http_only,
        }


class CookieManager(QObject):
    """Manages cookies for a profile."""

    cookies_loaded = pyqtSignal(list)  # Emitted when cookies are loaded

    def __init__(
        self, profile: QWebEngineProfile, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.profile = profile
        self.cookie_store = profile.cookieStore()
        self.cookies: list[CookieInfo] = []
        self._closed = False
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(
            lambda: self.cookies_loaded.emit(self.get_all_cookies())
        )

        # Connect to cookie store signals
        self.cookie_store.cookieAdded.connect(self._on_cookie_added)
        self.cookie_store.cookieRemoved.connect(self._on_cookie_removed)

    def close(self) -> None:
        """Detach from the store when switching profiles or closing a window."""
        if self._closed:
            return
        self._closed = True
        self._update_timer.stop()
        self.cookie_store.cookieAdded.disconnect(self._on_cookie_added)
        self.cookie_store.cookieRemoved.disconnect(self._on_cookie_removed)
        self.deleteLater()

    def _on_cookie_added(self, cookie: QNetworkCookie) -> None:
        """Handle when a cookie is added."""
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
        self.cookie_store.deleteCookie(cookie_info.cookie)

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
