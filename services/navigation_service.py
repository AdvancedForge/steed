"""Qt-thread page controls shared by ordinary tabs and internal task pages."""

from urllib.parse import quote

from PyQt6.QtCore import QObject, QUrl

import webengine
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
        from services.start_pages import internal_url

        internal = internal_url(text)
        if internal is not None:
            return internal
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
        if operation not in ("back", "forward", "reload", "stop"):
            raise ValueError("Unsupported navigation action")
        method = getattr(target, operation, None)
        if callable(method):
            method()
        else:
            webengine.trigger(target, operation)

    def find(
        self, page, text: str, *, backward=False, case_sensitive=False, callback=None
    ):
        require_owner_thread(self)
        webengine.find_text(
            page,
            text,
            backward=backward,
            case_sensitive=case_sensitive,
            callback=callback,
        )

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
