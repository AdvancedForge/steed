"""Shared Qt WebEngine settings and site compatibility helpers."""

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import (
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)

# qutebrowser uses this site-specific workaround for Google sign-in:
# https://github.com/qutebrowser/qutebrowser/issues/5182
# Keep it restricted to the account host; Firefox identification elsewhere can
# cause sites to select features that Chromium does not implement.
GOOGLE_LOGIN_USER_AGENT = (
    b"Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0"
)


def google_login_user_agent(url: QUrl) -> bytes | None:
    """Return the compatibility header for the HTTPS Google account host."""
    if url.scheme() == "https" and url.host() == "accounts.google.com":
        return GOOGLE_LOGIN_USER_AGENT
    return None


class SiteCompatibilityInterceptor(QWebEngineUrlRequestInterceptor):
    """Apply account-host compatibility after profile-level ad filtering."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.last_main_method = b""
        self.last_main_url = QUrl()

    @property
    def enabled(self):
        return self.config is not None and bool(
            self.config.get("google_login_compatibility", True)
        )

    def interceptRequest(self, info):
        url = info.requestUrl()
        if (
            info.resourceType()
            == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame
        ):
            self.last_main_method = bytes(info.requestMethod())
            self.last_main_url = url
        if self.enabled:
            user_agent = google_login_user_agent(url)
            if user_agent is not None:
                info.setHttpHeader(b"User-Agent", user_agent)


def configure_web_settings(settings: QWebEngineSettings) -> None:
    """Apply the browser's existing media and rendering preferences."""
    attributes = QWebEngineSettings.WebAttribute
    for attribute in (
        attributes.PluginsEnabled,
        attributes.PdfViewerEnabled,
        attributes.FullScreenSupportEnabled,
        attributes.WebGLEnabled,
        attributes.Accelerated2dCanvasEnabled,
        attributes.AutoLoadImages,
    ):
        settings.setAttribute(attribute, True)
    settings.setAttribute(attributes.PlaybackRequiresUserGesture, False)
