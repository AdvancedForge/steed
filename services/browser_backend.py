"""Replaceable session backend, initially implemented with private Qt pages."""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QObject, QThread, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QWidget

from services.command_broker import require_owner_thread
from services.navigation_service import NavigationService
from services.permission_service import PermissionService


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    task_id: str
    url: str
    title: str
    loading: bool
    revision: int
    load_success: bool | None


class BrowserBackend(Protocol):
    """Internal interface; DOM inspection/actions and grants are later milestones."""

    def open_session(self, task_id: str) -> SessionSnapshot: ...
    def navigate(self, session_id: str, url: str) -> SessionSnapshot: ...
    def inspect(self, session_id: str) -> SessionSnapshot: ...
    def stop(self, session_id: str) -> SessionSnapshot: ...
    def attach_view(
        self, session_id: str, parent: QWidget | None = None
    ) -> QWebEngineView: ...
    def close_session(self, session_id: str) -> None: ...
    def close(self) -> None: ...


def navigation_url(url: str) -> QUrl:
    """Validate before either journal persistence or engine dispatch."""
    if not isinstance(url, str):
        raise ValueError("Provide an HTTP/HTTPS URL or about:blank")
    qurl = QUrl(url)
    if (
        not qurl.isValid()
        or qurl.userName()
        or qurl.password()
        or not (
            (qurl.scheme() in ("http", "https") and bool(qurl.host()))
            or qurl.toString() == "about:blank"
        )
    ):
        raise ValueError("Provide an HTTP/HTTPS URL without credentials or about:blank")
    return qurl


class TaskPage(QWebEnginePage):
    """Suppress dialogs, uploads, and unsupported main-document schemes."""

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if not is_main_frame:
            return url.scheme() in ("http", "https", "about", "data", "blob")
        return (
            url.isValid()
            and not url.userName()
            and not url.password()
            and (
                (url.scheme() in ("http", "https") and bool(url.host()))
                or url.toString() == "about:blank"
            )
        )

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        return []

    def javaScriptAlert(self, origin, message):
        pass

    def javaScriptConfirm(self, origin, message):
        return False

    def javaScriptPrompt(self, origin, message, default_value):
        return False, ""


@dataclass
class QtSession:
    task_id: str
    profile: QWebEngineProfile
    page: TaskPage
    views: list[QWebEngineView] = field(default_factory=list)
    revision: int = 0
    loading: bool = False
    load_success: bool | None = None


class QtBrowserBackend(QObject):
    changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        app = QApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Qt browser backends require the application thread")
        self._sessions: dict[str, QtSession] = {}
        self.navigation = NavigationService(self)
        self._closed = False

    def _session(self, session_id):
        require_owner_thread(self)
        if self._closed:
            raise RuntimeError("Browser backend is closed")
        if session_id not in self._sessions:
            raise KeyError("Unknown session")
        return self._sessions[session_id]

    def open_session(self, task_id):
        require_owner_thread(self)
        if self._closed:
            raise RuntimeError("Browser backend is closed")
        session_id = uuid4().hex
        # An unnamed profile is off the record, separate from user/global profiles.
        profile = QWebEngineProfile(self)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        profile.downloadRequested.connect(lambda request: request.cancel())
        page = TaskPage(profile, self)
        page.setAudioMuted(True)
        PermissionService.deny_page(page, lambda: self._closed)
        session = QtSession(task_id, profile, page)
        self._sessions[session_id] = session
        page.loadStarted.connect(lambda: self._started(session_id))
        page.loadFinished.connect(lambda ok: self._finished(session_id, ok))
        page.urlChanged.connect(lambda url: self._updated(session_id))
        page.titleChanged.connect(lambda title: self._updated(session_id))
        page.renderProcessTerminated.connect(
            lambda *_: self._finished(session_id, False)
        )
        return self.inspect(session_id)

    def _updated(self, session_id):
        if session_id in self._sessions and not self._closed:
            self.changed.emit(self.inspect(session_id))

    def _started(self, session_id):
        if session_id not in self._sessions:
            return
        session = self._sessions[session_id]
        session.revision += 1
        session.loading, session.load_success = True, None
        self._updated(session_id)

    def _finished(self, session_id, ok):
        if session_id not in self._sessions:
            return
        session = self._sessions[session_id]
        session.loading, session.load_success = False, bool(ok)
        self._updated(session_id)

    def inspect(self, session_id):
        session = self._session(session_id)
        return SessionSnapshot(
            session_id,
            session.task_id,
            session.page.url().toString(),
            session.page.title()[:512],
            session.loading,
            session.revision,
            session.load_success,
        )

    def navigate(self, session_id, url):
        session = self._session(session_id)
        qurl = navigation_url(url)
        session.loading, session.load_success = True, None
        self.navigation.navigate(session.page, qurl)
        return self.inspect(session_id)

    def stop(self, session_id):
        session = self._session(session_id)
        self.navigation.action(session.page, "stop")
        session.loading, session.load_success = False, False
        return self.inspect(session_id)

    def attach_view(self, session_id, parent=None):
        session = self._session(session_id)
        session.views = [view for view in session.views if not sip.isdeleted(view)]
        if session.views:
            return session.views[0]
        view = QWebEngineView(parent)
        view.setPage(session.page)
        session.views.append(view)
        return view

    def close_session(self, session_id):
        session = self._session(session_id)
        self._sessions.pop(session_id)
        for view in session.views:
            if not sip.isdeleted(view):
                sip.delete(view)
        session.page.blockSignals(True)
        self.navigation.action(session.page, "stop")
        sip.delete(session.page)
        sip.delete(session.profile)

    def close(self):
        require_owner_thread(self)
        if self._closed:
            return
        for session_id in list(self._sessions):
            self.close_session(session_id)
        self._closed = True
