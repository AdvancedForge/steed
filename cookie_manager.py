"""Cookie management for profiles."""
from pathlib import Path
from typing import List, Dict, Optional
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineCookieStore
from PyQt6.QtCore import QObject, pyqtSignal, QUrl
from PyQt6.QtNetwork import QNetworkCookie


class CookieInfo:
    """Information about a cookie."""
    
    def __init__(self, cookie: QNetworkCookie):
        self.cookie = cookie
        self.name = cookie.name().data().decode('utf-8', errors='ignore')
        self.value = cookie.value().data().decode('utf-8', errors='ignore')
        self.domain = cookie.domain()
        self.path = cookie.path()
        self.expiration_date = cookie.expirationDate()
        self.is_secure = cookie.isSecure()
        self.is_http_only = cookie.isHttpOnly()
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "expiration": self.expiration_date.toString() if self.expiration_date.isValid() else None,
            "secure": self.is_secure,
            "http_only": self.is_http_only
        }


class CookieManager(QObject):
    """Manages cookies for a profile."""
    
    cookies_loaded = pyqtSignal(list)  # Emitted when cookies are loaded
    
    def __init__(self, profile: QWebEngineProfile):
        super().__init__()
        self.profile = profile
        self.cookie_store = profile.cookieStore()
        self.cookies: List[CookieInfo] = []
        
        # Connect to cookie store signals
        self.cookie_store.cookieAdded.connect(self._on_cookie_added)
        self.cookie_store.cookieRemoved.connect(self._on_cookie_removed)
    
    def _on_cookie_added(self, cookie: QNetworkCookie):
        """Handle when a cookie is added."""
        cookie_info = CookieInfo(cookie)
        # Update or add to list
        existing = next((c for c in self.cookies if c.name == cookie_info.name and 
                        c.domain == cookie_info.domain and c.path == cookie_info.path), None)
        if existing:
            self.cookies.remove(existing)
        self.cookies.append(cookie_info)
    
    def _on_cookie_removed(self, cookie: QNetworkCookie):
        """Handle when a cookie is removed."""
        cookie_info = CookieInfo(cookie)
        self.cookies = [c for c in self.cookies if not (
            c.name == cookie_info.name and 
            c.domain == cookie_info.domain and 
            c.path == cookie_info.path
        )]
    
    def load_all_cookies(self):
        """Load all cookies from the store."""
        self.cookies.clear()
        self.cookie_store.loadAllCookies()
    
    def get_cookies_for_domain(self, domain: str) -> List[CookieInfo]:
        """Get cookies for a specific domain."""
        return [c for c in self.cookies if domain in c.domain or c.domain in domain]
    
    def delete_cookie(self, cookie_info: CookieInfo):
        """Delete a specific cookie."""
        self.cookie_store.deleteCookie(cookie_info.cookie)
    
    def delete_cookies_for_domain(self, domain: str):
        """Delete all cookies for a domain."""
        cookies_to_delete = self.get_cookies_for_domain(domain)
        for cookie_info in cookies_to_delete:
            self.delete_cookie(cookie_info)
    
    def clear_all_cookies(self):
        """Clear all cookies."""
        self.cookie_store.deleteAllCookies()
        self.cookies = []
    
    def get_all_cookies(self) -> List[CookieInfo]:
        """Get all cookies."""
        return self.cookies.copy()
