"""Replaceable session backend with private or exclusively leased Qt profiles."""

import threading
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QWidget

from services.command_broker import require_owner_thread
from services.navigation_service import NavigationService
from services.page_inspector import PageInspectionError, PageInspector
from services.permission_service import PermissionService
from services.task_policy import LiveGrant


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
    """Internal interface; each engine session must receive approved authority."""

    def open_session(
        self, task_id: str, *, grant: LiveGrant, profile: str = "private"
    ) -> SessionSnapshot: ...
    def navigate(self, session_id: str, url: str) -> SessionSnapshot: ...
    def inspect(self, session_id: str) -> SessionSnapshot: ...
    def inspect_page(self, session_id: str): ...
    def inspect_target(self, session_id: str, snapshot_id: str, target_id: str): ...
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

    def __init__(self, profile, parent, grant, blocked):
        super().__init__(profile, parent)
        self.grant = grant
        self.report_blocked = blocked

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        reason = self.grant.navigation(url.toString())
        if reason:
            self.report_blocked(reason)
        return not reason

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        return []

    def javaScriptAlert(self, origin, message):
        pass

    def javaScriptConfirm(self, origin, message):
        return False

    def javaScriptPrompt(self, origin, message, default_value):
        return False, ""


class TaskRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Profile-wide scope covers redirects/resources without Qt/store lookups."""

    def __init__(self, grant, blocked, parent, fallback=None):
        super().__init__(parent)
        self.grant = grant
        self.report_blocked = blocked
        self._reported = set()
        self._lock = threading.Lock()
        self.fallback = fallback

    def interceptRequest(self, info):
        resource = QWebEngineUrlRequestInfo.ResourceType
        forbidden = (
            resource.ResourceTypeWorker,
            resource.ResourceTypeSharedWorker,
            resource.ResourceTypeServiceWorker,
            getattr(resource, "ResourceTypeWebSocket", None),
        )
        reason = (
            "active_content_unavailable"
            if info.resourceType() in forbidden
            else self.grant.request(
                info.requestUrl().toString(), bytes(info.requestMethod())
            )
        )
        if reason:
            info.block(True)
            # Bound journal traffic from a page repeatedly making denied requests.
            with self._lock:
                fresh = reason not in self._reported
                self._reported.add(reason)
            if fresh:
                self.report_blocked(reason)
        elif self.fallback is not None:
            self.fallback.interceptRequest(info)


@dataclass
class QtSession:
    task_id: str
    profile: QWebEngineProfile
    page: TaskPage
    grant: LiveGrant
    owns_profile: bool = True
    views: list[QWebEngineView] = field(default_factory=list)
    revision: int = 0
    loading: bool = False
    load_success: bool | None = None


class QtBrowserBackend(QObject):
    changed = pyqtSignal(object)
    blocked = pyqtSignal(str, str)

    def __init__(self, parent=None, *, profile_provider=None):
        super().__init__(parent)
        app = QApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Qt browser backends require the application thread")
        self._sessions: dict[str, QtSession] = {}
        self.navigation = NavigationService(self)
        self.inspector = PageInspector(self)
        self._closed = False
        self.profile_provider = profile_provider

    def _session(self, session_id):
        require_owner_thread(self)
        if self._closed:
            raise RuntimeError("Browser backend is closed")
        if session_id not in self._sessions:
            raise KeyError("Unknown session")
        return self._sessions[session_id]

    def open_session(self, task_id, *, grant, profile="private"):
        require_owner_thread(self)
        if self._closed:
            raise RuntimeError("Browser backend is closed")
        grant.check("open", profile=profile)
        session_id = uuid4().hex
        owns_profile = profile == "private"
        if owns_profile:
            profile = QWebEngineProfile(self)
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        elif self.profile_provider is not None:
            profile = self.profile_provider(task_id, profile)
        else:
            raise ValueError("Existing profiles require the application registry")

        def report(reason):
            self.blocked.emit(task_id, reason)

        if owns_profile:
            interceptor = TaskRequestInterceptor(grant, report, profile)
            profile.setUrlRequestInterceptor(interceptor)
            profile.task_interceptor = interceptor
            profile.downloadRequested.connect(lambda request: request.cancel())
        page = TaskPage(profile, self, grant, report)
        # Page overrides preserve the ordinary profile's rendering preferences.
        settings = page.settings()
        settings.setUnknownUrlSchemePolicy(
            QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes
        )
        # Current scopes authorize document retrieval, not active page execution.
        for attribute in (
            QWebEngineSettings.WebAttribute.JavascriptEnabled,
            QWebEngineSettings.WebAttribute.DnsPrefetchEnabled,
            QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled,
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            QWebEngineSettings.WebAttribute.PluginsEnabled,
            QWebEngineSettings.WebAttribute.PdfViewerEnabled,
        ):
            settings.setAttribute(attribute, False)

        page.setAudioMuted(True)
        page.fullScreenRequested.connect(lambda request: request.reject())
        page.registerProtocolHandlerRequested.connect(lambda request: request.reject())
        page.selectClientCertificate.connect(lambda selection: selection.selectNone())
        PermissionService.deny_page(page, lambda: self._closed)
        session = QtSession(task_id, profile, page, grant, owns_profile)
        self._sessions[session_id] = session
        page.loadStarted.connect(lambda: self._started(session_id))
        page.loadFinished.connect(lambda ok: self._finished(session_id, ok))
        page.urlChanged.connect(lambda url: self._url_changed(session_id))
        page.titleChanged.connect(lambda title: self._updated(session_id))
        page.renderProcessTerminated.connect(lambda *_: self._terminated(session_id))
        return self.inspect(session_id)

    def _updated(self, session_id):
        if session_id in self._sessions and not self._closed:
            self.changed.emit(self.inspect(session_id))

    def _url_changed(self, session_id):
        self.inspector.invalidate(session_id)
        self._updated(session_id)

    def _terminated(self, session_id):
        self.inspector.invalidate(session_id, "renderer_terminated")
        self._finished(session_id, False)

    def _started(self, session_id):
        if session_id not in self._sessions:
            return
        session = self._sessions[session_id]
        self.inspector.invalidate(session_id)
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
        session.grant.check("navigate", url=url)
        qurl = navigation_url(url)
        self.inspector.invalidate(session_id)
        session.loading, session.load_success = True, None
        self.navigation.navigate(session.page, qurl)
        return self.inspect(session_id)

    def stop(self, session_id):
        session = self._session(session_id)
        self.inspector.invalidate(session_id, "session_stopped")
        self.navigation.action(session.page, "stop")
        session.loading, session.load_success = False, False
        return self.inspect(session_id)

    def _read_context(self, session_id):
        session = self._session(session_id)
        if session.loading or session.load_success is not True:
            raise PageInspectionError("page_not_ready")
        session.grant.check("read", url=session.page.url().toString())
        revision = session.revision

        def validate():
            current = self._session(session_id)
            current.grant.check("read", url=current.page.url().toString())
            if current.revision != revision or current.loading:
                raise PageInspectionError("stale_snapshot")

        return session, validate

    def inspect_page(self, session_id):
        session, validate = self._read_context(session_id)
        return self.inspector.inspect(
            self.inspect(session_id), session.page, session.grant, validate
        )

    def inspect_target(self, session_id, snapshot_id, target_id):
        session, validate = self._read_context(session_id)
        return self.inspector.target(
            session_id, session.page, snapshot_id, target_id, validate
        )

    def attach_view(self, session_id, parent=None):
        session = self._session(session_id)
        session.grant.check("attach")
        session.views = [view for view in session.views if not sip.isdeleted(view)]
        if session.views:
            return session.views[0]
        view = QWebEngineView(parent)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        view.setPage(session.page)
        session.views.append(view)
        return view

    def close_session(self, session_id):
        session = self._session(session_id)
        self.inspector.invalidate(session_id, "session_closed", discard=True)
        self._sessions.pop(session_id)
        for view in session.views:
            if not sip.isdeleted(view):
                sip.delete(view)
        session.page.blockSignals(True)
        self.navigation.action(session.page, "stop")
        sip.delete(session.page)
        if session.owns_profile:
            sip.delete(session.profile)

    def close(self):
        require_owner_thread(self)
        if self._closed:
            return
        for session_id in list(self._sessions):
            self.close_session(session_id)
        self._closed = True
