"""Qt-thread page controls shared by ordinary tabs and internal task pages."""

from urllib.parse import quote

from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage

from services.command_broker import require_owner_thread

ZOOM_LEVELS = (
    0.25,
    0.33,
    0.5,
    0.67,
    0.75,
    0.9,
    1.0,
    1.1,
    1.25,
    1.5,
    1.75,
    2.0,
    2.5,
    3.0,
    4.0,
    5.0,
)


class NavigationService(QObject):
    """Accept engine pages or views; address parsing belongs only to user browsing."""

    @staticmethod
    def address_url(text: str, search_engine: str) -> QUrl:
        text = (text or "").strip()
        if text.startswith(("http://", "https://", "file://", "about:")):
            return QUrl(text)
        if " " not in text and ("." in text or text.startswith(("localhost", "["))):
            scheme = (
                "http" if text.startswith(("localhost", "127.", "[::1]")) else "https"
            )
            return QUrl(f"{scheme}://{text}")
        return QUrl(f"{search_engine}{quote(text, safe='')}")

    def navigate(self, target, url: QUrl):
        require_owner_thread(self)
        target.setUrl(url)

    def action(self, target, operation: str):
        require_owner_thread(self)
        actions = {
            "back": QWebEnginePage.WebAction.Back,
            "forward": QWebEnginePage.WebAction.Forward,
            "reload": QWebEnginePage.WebAction.Reload,
            "stop": QWebEnginePage.WebAction.Stop,
        }
        if operation not in actions:
            raise ValueError("Unsupported navigation action")
        method = getattr(target, operation, None)
        if callable(method):
            method()
        else:
            target.triggerAction(actions[operation])

    def find(
        self, page, text: str, *, backward=False, case_sensitive=False, callback=None
    ):
        require_owner_thread(self)
        flags = QWebEnginePage.FindFlag(0)
        if backward:
            flags |= QWebEnginePage.FindFlag.FindBackward
        if case_sensitive:
            flags |= QWebEnginePage.FindFlag.FindCaseSensitively
        if callback is None:
            page.findText(text, flags)
        else:
            page.findText(text, flags, callback)

    def zoom(self, target, direction: int = 0) -> float:
        require_owner_thread(self)
        current = target.zoomFactor()
        if direction > 0:
            factor = next(
                (level for level in ZOOM_LEVELS if level > current + 0.001),
                ZOOM_LEVELS[-1],
            )
        elif direction < 0:
            factor = next(
                (level for level in reversed(ZOOM_LEVELS) if level < current - 0.001),
                ZOOM_LEVELS[0],
            )
        else:
            factor = 1.0
        target.setZoomFactor(factor)
        return target.zoomFactor()
