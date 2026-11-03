"""Replaceable session backend with private or collaboratively shared profiles."""

import json
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QWidget

import webengine
from services.command_broker import require_owner_thread
from services.engine_capabilities import qt_engine_capabilities
from services.form_submission import SubmissionTicket, journal_origin
from services.navigation_service import NavigationService
from services.page_actions import (
    FileUploadResult,
    FormSubmissionResult,
    PageActionResult,
    PageWaitResult,
    action_arguments,
    scroll_arguments,
    wait_arguments,
)
from services.page_inspector import (
    PageInspectionError,
    PageInspector,
    content_signature,
)
from services.permission_service import PermissionService
from services.task_downloads import TaskDownload
from services.task_network import TaskNetwork
from services.task_policy import LiveGrant
from services.task_uploads import UploadCopy


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    task_id: str
    url: str
    title: str
    loading: bool
    revision: int
    load_success: bool | None
    control_owner: str = "agent"
    mode: str = "restricted"


@dataclass(frozen=True)
class NavigationResult:
    session_id: str
    url: str
    revision: int
    status: str


class BrowserBackend(Protocol):
    """Internal interface; each engine session must receive approved authority."""

    def capabilities(self): ...

    def open_session(
        self, task_id: str, *, grant: LiveGrant, profile: str = "private"
    ) -> SessionSnapshot: ...
    def navigate(self, session_id: str, url: str) -> SessionSnapshot: ...
    def inspect(self, session_id: str) -> SessionSnapshot: ...
    def inspect_page(self, session_id: str): ...
    def capture_page(self, session_id: str): ...
    def inspect_target(self, session_id: str, snapshot_id: str, target_id: str): ...
    def navigate_observed(self, session_id: str, url: str): ...
    def act(self, session_id, action_id, snapshot_id, target_id, arguments): ...
    def submit_form(self, session_id, action_id, snapshot_id, target_id): ...
    def scroll(
        self, session_id, action_id, snapshot_id, frame_id, arguments, target_id=None
    ): ...
    def wait(self, session_id, condition, text, timeout_ms): ...
    def start_download(self, session_id, download_id, url, destination, publish): ...
    def upload(self, session_id, action_id, snapshot_id, target_id, source): ...
    def inspect_download(self, session_id, download_id): ...
    def list_downloads(self, session_id): ...
    def cancel_download(self, session_id, download_id): ...
    def check_control_change(self, session_id): ...
    def change_control(self, session_id, owner): ...
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


# Lazy content usually arrives within a request round trip; require this long
# without change before reporting a page as settled.
SETTLE_MS = 750


class TaskPage(QWebEnginePage):
    """Suppress dialogs, uploads, and unsupported main-document schemes."""

    def __init__(self, profile, parent, grant, blocked, *, live=False):
        super().__init__(profile, parent)
        self.grant = grant
        self.live = live
        self.report_blocked = blocked
        self.is_task_page = True
        self.network_ready = True
        self.submission = SubmissionTicket(grant)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if not self.network_ready and url.toString() != "about:blank":
            return False
        if self.live and not is_main_frame:
            # Embedded sites are normal background activity in live browsing.
            # DOM reads still independently check each frame's origin.
            return url.scheme() in (
                "http",
                "https",
                "blob",
                "data",
            ) or url.toString() in ("about:blank", "about:srcdoc")
        reason = self.grant.navigation(url.toString())
        if (
            not reason
            and not self.live
            and navigation_type == self.NavigationType.NavigationTypeFormSubmitted
        ):
            reason = self.submission.navigation(url.toString(), is_main_frame)
        elif (
            not reason
            and not self.live
            and navigation_type == self.NavigationType.NavigationTypeRedirect
        ):
            reason = self.submission.redirect(is_main_frame)
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


REDIRECT_GUARD_SCRIPT = """
(() => {
  const add = () => {
    const meta = document.createElement('meta');
    meta.httpEquiv = 'Content-Security-Policy';
    meta.content = %s;
    document.head.prepend(meta);
  };
  if (document.head) { add(); return; }
  const observer = new MutationObserver(() => {
    if (document.head) { observer.disconnect(); add(); }
  });
  observer.observe(document, {childList: true, subtree: true});
})();
"""


def redirect_guard(grant):
    """Renderer-enforced scope for subresource and frame redirect hops.

    Qt 6.11 can follow a redirect before an interceptor block lands, and
    DevTools Fetch cannot hold or rewrite these requests. Blink checks the
    document's CSP before following each hop, so this backs up the
    interceptor. Restricted pages run no page script that could remove it.
    """
    origins = sorted(grant.scope.origins)
    if not origins or any("[" in origin for origin in origins):
        # CSP host sources cannot express IPv6 literals; keep the interceptor.
        return None
    sources = " ".join(origins)
    policy = (
        f"default-src {sources}; style-src {sources} 'unsafe-inline'; "
        f"img-src {sources} data: blob:; font-src {sources} data:; "
        f"media-src {sources} data: blob:; script-src 'none'; object-src 'none'"
    )
    return webengine.page_script(
        "steed-redirect-guard",
        REDIRECT_GUARD_SCRIPT % json.dumps(policy),
        when="start",
        frames=True,
    )


class TaskRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Scope redirects/resources without Qt/store lookups in network callbacks."""

    def __init__(self, grant, blocked, parent, fallback=None, *, submission=None):
        super().__init__(parent)
        self.grant = grant
        self.report_blocked = blocked
        self._reported = set()
        self._lock = threading.Lock()
        self.fallback = fallback
        self.submission = submission

    def interceptRequest(self, info):
        resource = QWebEngineUrlRequestInfo.ResourceType
        forbidden = (
            resource.ResourceTypeWorker,
            resource.ResourceTypeSharedWorker,
            resource.ResourceTypeServiceWorker,
            getattr(resource, "ResourceTypeWebSocket", None),
        )
        url, method = info.requestUrl().toString(), bytes(info.requestMethod())
        form = info.navigationType() == info.NavigationType.NavigationTypeFormSubmitted
        if info.resourceType() in forbidden:
            reason = "active_content_unavailable"
        elif self.submission is not None and form:
            reason = self.submission.request(
                url, method, info.resourceType() == resource.ResourceTypeMainFrame
            )
        else:
            reason = self.grant.request(url, method)
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
    page: QWebEnginePage
    grant: LiveGrant
    owns_profile: bool = True
    views: list[QWebEngineView] = field(default_factory=list)
    revision: int = 0
    loading: bool = False
    load_success: bool | None = None
    network: TaskNetwork | None = None
    pending_navigation: Future | None = None
    pending_action: Future | None = None
    waits: set[Future] = field(default_factory=set)
    control_owner: str = "agent"
    downloads: dict = field(default_factory=dict)
    uploads: dict = field(default_factory=dict)
    borrowed_view: QWebEngineView | None = None
    connections: list = field(default_factory=list)
    shield: QObject | None = None
    focus_policy: Qt.FocusPolicy | None = None
    mode: str = "restricted"
    computer_capture: dict | None = None


class QtBrowserBackend(QObject):
    changed = pyqtSignal(object)
    blocked = pyqtSignal(str, str)

    def __init__(self, parent=None, *, profile_provider=None, browser_config=None):
        super().__init__(parent)
        app = QApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Qt browser backends require the application thread")
        self._sessions: dict[str, QtSession] = {}
        self.navigation = NavigationService(self)
        self.inspector = PageInspector(self)
        self._closed = False
        self.profile_provider = profile_provider
        self.browser_config = browser_config
        self.navigation_timeout_ms = 15000
        self.input_timeout_ms = 10000

    def _session(self, session_id):
        require_owner_thread(self)
        if self._closed:
            raise RuntimeError("Browser backend is closed")
        if session_id not in self._sessions:
            raise KeyError("Unknown session")
        return self._sessions[session_id]

    def capabilities(self):
        require_owner_thread(self)
        return qt_engine_capabilities()

    def open_session(self, task_id, *, grant, profile="private", mode="restricted"):
        require_owner_thread(self)
        if self._closed:
            raise RuntimeError("Browser backend is closed")
        grant.check("open", profile=profile)
        if mode not in ("restricted", "live"):
            raise ValueError("Use restricted or live browsing mode")
        if mode == "live":
            grant.check("live", profile=profile)
            grant.check("read")
        session_id = uuid4().hex
        owns_profile = profile == "private"
        if owns_profile:
            profile = webengine.private_profile(self)
            if mode == "live":
                from webengine import configure_web_settings

                configure_web_settings(profile.settings())
                if self.browser_config is not None:
                    override = self.browser_config.get(
                        "user_agent_override", ""
                    ).strip()
                    if override:
                        profile.setHttpUserAgent(override)
        elif self.profile_provider is not None:
            profile = self.profile_provider(task_id, profile)
        else:
            raise ValueError("Existing profiles require the application registry")

        def report(reason):
            self.blocked.emit(task_id, reason)

        page = TaskPage(profile, self, grant, report, live=mode == "live")
        if owns_profile and mode == "restricted":
            interceptor = TaskRequestInterceptor(
                grant, report, profile, submission=page.submission
            )
            profile.setUrlRequestInterceptor(interceptor)
            profile.task_interceptor = interceptor
            profile.downloadRequested.connect(
                lambda request: page.task_download_handler(request)
            )
        if not owns_profile and mode == "restricted":
            interceptor = TaskRequestInterceptor(
                grant, report, page, submission=page.submission
            )
            page.setUrlRequestInterceptor(interceptor)
            page.task_interceptor = interceptor
            page.network_ready = False
        # Page overrides preserve the ordinary profile's rendering preferences.
        settings = page.settings()
        webengine.block_unknown_schemes(settings)
        # Current scopes authorize document retrieval, not active page execution.
        webengine.set_options(
            settings,
            javascript=mode == "live",
            dns_prefetch=False,
            hyperlink_auditing=False,
            local_files_read_files=False,
            local_files_reach_web=False,
            plugins=False,
            pdf_viewer=False,
        )
        if mode != "live":
            guard = redirect_guard(grant)
            if guard is not None:
                page.scripts().insert(guard)
        if owns_profile and mode == "live":
            profile.downloadRequested.connect(
                lambda request: page.task_download_handler(request)
            )
        page.setAudioMuted(True)
        page.fullScreenRequested.connect(lambda request: request.reject())
        page.registerProtocolHandlerRequested.connect(lambda request: request.reject())
        page.selectClientCertificate.connect(lambda selection: selection.selectNone())
        PermissionService.deny_page(page, lambda: self._closed)
        session = QtSession(task_id, profile, page, grant, owns_profile, mode=mode)
        self._sessions[session_id] = session
        page.task_download_handler = lambda request: self._download_requested(
            session_id, request
        )
        page.loadStarted.connect(lambda: self._started(session_id))
        page.loadFinished.connect(lambda ok: self._finished(session_id, ok))
        page.urlChanged.connect(lambda url: self._url_changed(session_id))
        page.titleChanged.connect(lambda title: self._updated(session_id))
        page.renderProcessTerminated.connect(lambda *_: self._terminated(session_id))
        if not owns_profile and mode == "restricted":
            page.setUrl(QUrl("about:blank"))
            session.network = TaskNetwork(page, self)
        return self.inspect(session_id)

    def adopt_session(self, task_id, view, *, grant, profile):
        """Borrow the actual user page without reload, settings or network overrides."""
        require_owner_thread(self)
        if self._closed or sip.isdeleted(view):
            raise RuntimeError("Live tab unavailable")
        page = view.page()
        grant.check("live", profile=profile, url=page.url().toString())
        grant.check("read", url=page.url().toString())
        if self.profile_provider is None or page.profile() is not self.profile_provider(
            task_id, profile
        ):
            raise ValueError("Live tab profile does not match approval")
        if self.live_session(view) is not None:
            raise ValueError("This tab is already delegated")
        if page.devToolsPage() is not None:
            raise ValueError("Close this tab's Developer Tools before delegating")
        from ui.task_watch import InputShield

        session_id = uuid4().hex
        session = QtSession(task_id, page.profile(), page, grant, False)
        session.borrowed_view = view
        session.mode = "live"
        session.focus_policy = view.focusPolicy()
        session.shield = InputShield(view)
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.clearFocus()
        session.loading = page.isLoading()
        session.load_success = None if session.loading else True
        self._sessions[session_id] = session
        for signal, callback in (
            (page.loadStarted, lambda: self._started(session_id)),
            (page.loadFinished, lambda ok: self._finished(session_id, ok)),
            (page.urlChanged, lambda _: self._url_changed(session_id)),
            (page.titleChanged, lambda _: self._updated(session_id)),
            (page.renderProcessTerminated, lambda *_: self._terminated(session_id)),
        ):
            signal.connect(callback)
            session.connections.append((signal, callback))
        return self.inspect(session_id)

    def live_session(self, view):
        require_owner_thread(self)
        return next(
            (
                key
                for key, session in self._sessions.items()
                if session.borrowed_view is view
            ),
            None,
        )

    def live_view(self, session_id):
        return self._session(session_id).borrowed_view

    def _updated(self, session_id):
        if session_id in self._sessions and not self._closed:
            self.changed.emit(self.inspect(session_id))

    def _url_changed(self, session_id):
        self.inspector.invalidate(session_id)
        self._updated(session_id)

    def _terminated(self, session_id):
        self.inspector.invalidate(session_id, "renderer_terminated")
        if session_id in self._sessions:
            session = self._sessions[session_id]
            self._cancel_action(session, "renderer_terminated")
            self._cancel_waits(session, "renderer_terminated")
            if session.network is not None:
                session.network._fail()
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
            session.control_owner,
            session.mode,
        )

    def navigate(self, session_id, url):
        session = self._session(session_id)
        self._cancel_action(session, "navigation_superseded")
        self._cancel_waits(session, "navigation_superseded")
        return self._navigate(session_id, url)

    def _navigate(self, session_id, url):
        session = self._session(session_id)
        session.grant.check("navigate", url=url)
        qurl = navigation_url(url)
        if session.network is not None:
            result = Future()
            if session.pending_navigation is not None:
                raise RuntimeError("Task navigation is already pending")
            session.pending_navigation = result

            def prepared(ready):
                if result.done():
                    return
                session.pending_navigation = None
                try:
                    ready.result()
                    session.network.validate()
                    session.grant.check("navigate", url=url)
                    session.page.network_ready = True
                    result.set_result(self._navigate_ready(session_id, qurl))
                except Exception as error:
                    result.set_exception(error)

            session.network.ready.add_done_callback(prepared)
            return result
        return self._navigate_ready(session_id, qurl)

    def navigate_observed(self, session_id, url):
        """Report the observed load outcome, rather than dispatch as success."""
        session = self._session(session_id)
        self._cancel_action(session, "navigation_superseded")
        self._cancel_waits(session, "navigation_superseded")
        result = Future()
        session.pending_action = result
        timer = QTimer(self)
        timer.setSingleShot(True)
        dispatched = False
        previous_url = session.page.url()
        destination = navigation_url(url)
        previous_revision = session.revision
        fragment_only = (
            previous_url.adjusted(QUrl.UrlFormattingOption.RemoveFragment)
            == destination.adjusted(QUrl.UrlFormattingOption.RemoveFragment)
            and previous_url.fragment() != destination.fragment()
        )

        def cleanup(_):
            timer.stop()
            timer.deleteLater()
            if session.pending_action is result:
                session.pending_action = None
            session.page.loadFinished.disconnect(finished)
            session.page.urlChanged.disconnect(same_document)

        def same_document(*_):
            if (
                result.done()
                or not dispatched
                or not fragment_only
                or session.revision != previous_revision
                or session.page.isLoading()
                or session.page.url() != destination
            ):
                return
            session.loading, session.load_success = False, True
            self._updated(session_id)
            result.set_result(
                NavigationResult(
                    session_id,
                    destination.toString(),
                    session.revision,
                    "same_document",
                )
            )

        def finished(ok):
            if result.done() or not dispatched:
                return
            metadata = self.inspect(session_id)
            result.set_result(
                NavigationResult(
                    session_id,
                    metadata.url,
                    metadata.revision,
                    "loaded" if ok else "load_failed",
                )
            )

        def expire():
            if result.done():
                return
            metadata = self.inspect(session_id)
            result.set_result(
                NavigationResult(
                    session_id, metadata.url, metadata.revision, "timed_out"
                )
            )
            self.stop(session_id)

        def prepared(source):
            nonlocal dispatched
            if result.done():
                return
            try:
                if isinstance(source, Future):
                    source.result()
                dispatched = True
                timer.start(self.navigation_timeout_ms)
                same_document()
            except Exception as error:
                result.set_exception(error)

        session.page.loadFinished.connect(finished)
        session.page.urlChanged.connect(same_document)
        timer.timeout.connect(expire)
        result.add_done_callback(cleanup)
        try:
            source = self._navigate(session_id, url)
            if isinstance(source, Future):
                source.add_done_callback(prepared)
            else:
                prepared(source)
        except Exception as error:
            result.set_exception(error)
        return result

    def _navigate_ready(self, session_id, qurl):
        session = self._session(session_id)
        self.inspector.invalidate(session_id)
        session.loading, session.load_success = True, None
        self.navigation.navigate(session.page, qurl)
        return self.inspect(session_id)

    def stop(self, session_id):
        session = self._session(session_id)
        self._cancel_navigation(session)
        self._cancel_action(session, "session_stopped")
        self._cancel_waits(session, "session_stopped")
        self.inspector.invalidate(session_id, "session_stopped")
        self.navigation.action(session.page, "stop")
        session.loading, session.load_success = False, False
        return self.inspect(session_id)

    def _cancel_navigation(self, session):
        pending, session.pending_navigation = session.pending_navigation, None
        if pending is not None and not pending.done():
            pending.set_exception(RuntimeError("Task navigation cancelled"))

    def _cancel_action(self, session, reason):
        for transfer in session.downloads.values():
            if not transfer.result.done():
                transfer.cancel(reason)
        pending = session.pending_action
        if pending is not None and not pending.done():
            pending.set_exception(PageInspectionError(reason))

    def _cancel_waits(self, session, reason):
        for pending in tuple(session.waits):
            if not pending.done():
                pending.set_exception(PageInspectionError(reason))

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

    def capture_page(self, session_id):
        from services.page_capture import capture_viewport

        session, validate_read = self._read_context(session_id)
        view = session.borrowed_view or next(
            (v for v in session.views if not sip.isdeleted(v)), None
        )
        if view is None or not view.isVisible():
            raise PageInspectionError("visible_view_required")
        session.grant.check("visual", url=session.page.url().toString())
        if session.pending_action is not None:
            raise PageInspectionError("session_busy")
        if session.network is None:
            if session.page.devToolsPage() is not None:
                raise PageInspectionError("native_frontend_in_use")
            session.network = TaskNetwork(
                session.page, self, live=session.mode == "live"
            )
        revision = session.revision

        def validate():
            validate_read()
            session.grant.check("visual", url=session.page.url().toString())
            if sip.isdeleted(view) or not view.isVisible():
                raise PageInspectionError("visible_view_required")

        result = capture_viewport(session.network, validate)
        session.waits.add(result)
        result.add_done_callback(lambda _: session.waits.discard(result))
        response = Future()

        def completed(source):
            try:
                data = source.result()
                validate()
                capture_id = uuid4().hex
                session.computer_capture = {
                    "capture_id": capture_id,
                    "revision": revision,
                    "created": time.monotonic(),
                    **{
                        k: data[k]
                        for k in (
                            "width",
                            "height",
                            "css_width",
                            "css_height",
                            "viewport",
                        )
                    },
                }
                response.set_result(
                    {
                        "session_id": session_id,
                        "revision": revision,
                        "capture_id": capture_id,
                        **data,
                    }
                )
            except Exception as error:
                response.set_exception(error)

        result.add_done_callback(completed)
        return response

    def computer(self, session_id, action_id, capture_id, operation, values):
        from services.computer_input import KEYS, SCRIPT, arguments

        values = arguments(operation, values)
        session, validate_read = self._edit_context(session_id, "computer")
        session.grant.check("visual")
        session.grant.check("live")
        if session.mode != "live":
            raise PageInspectionError("live_session_required")
        capture = session.computer_capture
        if (
            not capture
            or capture["capture_id"] != capture_id
            or capture["revision"] != session.revision
            or time.monotonic() - capture["created"] > 60
            or not isinstance(capture["viewport"], dict)
        ):
            raise PageInspectionError("stale_capture")
        if operation == "click" and (
            values["x"] >= capture["width"] or values["y"] >= capture["height"]
        ):
            raise PageInspectionError("invalid_coordinates")
        view = session.borrowed_view or next(
            (v for v in session.views if not sip.isdeleted(v)), None
        )
        if view is None or not view.isVisible():
            raise PageInspectionError("visible_view_required")
        session.computer_capture = None  # One dispatch per screenshot; never replay.
        network = session.network
        if network is None:
            raise PageInspectionError("capture_required")
        result = Future()
        session.pending_action = result

        def validate_after():
            current = self._session(session_id)
            for action in ("computer", "read", "visual", "live"):
                current.grant.check(action, url=current.page.url().toString())
            if current.control_owner != "agent":
                raise PageInspectionError("user_control_active")
            if sip.isdeleted(view) or not view.isVisible():
                raise PageInspectionError("visible_view_required")

        def ready(source):
            if result.done():
                return
            try:
                source.result()
                validate_read()
                validate_after()
                request = {
                    "operation": operation,
                    "values": values,
                    "viewport": capture["viewport"],
                    "css_width": capture["css_width"],
                    "css_height": capture["css_height"],
                    "image_width": capture["width"],
                    "image_height": capture["height"],
                }
                if operation == "key":
                    request["key"] = KEYS[values["key"]]
                dispatched = network.input._run(SCRIPT, request, validate_after)
                dispatched.add_done_callback(completed)
            except Exception as error:
                result.set_exception(error)

        def completed(source):
            if result.done():
                return
            try:
                source.result()
                validate_after()
                self.inspector.invalidate(session_id)
                result.set_result(
                    {
                        "session_id": session_id,
                        "action_id": action_id,
                        "operation": operation,
                        "status": "dispatched",
                        "revision": session.revision,
                    }
                )
            except Exception as error:
                result.set_exception(error)

        def cleanup(_):
            if session.pending_action is result:
                session.pending_action = None

        result.add_done_callback(cleanup)
        network.ready.add_done_callback(ready)
        return result

    def inspect_target(self, session_id, snapshot_id, target_id):
        session, validate = self._read_context(session_id)
        return self.inspector.target(
            session_id, session.page, snapshot_id, target_id, validate
        )

    def _edit_context(self, session_id, action):
        session, validate_read = self._read_context(session_id)
        if session.pending_action is not None:
            raise PageInspectionError("action_in_progress")
        session.grant.check(action)
        self._cancel_waits(session, "action_started")

        def validate():
            validate_read()
            session.grant.check(action)

        return session, validate

    def act(self, session_id, action_id, snapshot_id, target_id, arguments):
        arguments = action_arguments(**arguments)
        session, validate = self._edit_context(session_id, "edit")
        if arguments["operation"] == "drag":
            if session.mode != "live":
                raise PageInspectionError("live_session_required")
            session.grant.check("submit")
            source = self._live_drag(
                session_id, snapshot_id, target_id, arguments["to_target_id"], validate
            )
            return self._action_result(
                session_id, action_id, "drag", source, target_id=target_id
            )
        if arguments["operation"] == "hover":
            if session.mode != "live":
                raise PageInspectionError("live_session_required")
            source = self._live_pointer(
                session_id, snapshot_id, target_id, validate, hover=True
            )
            return self._action_result(
                session_id, action_id, "hover", source, target_id=target_id
            )
        if session.mode == "live" and arguments["operation"] == "activate":
            _, descriptor = self.inspector._target_reference(
                session_id, snapshot_id, target_id
            )
            if descriptor.kind == "button":
                session.grant.check("submit")
                source = self._live_activate(
                    session_id, snapshot_id, target_id, "activate", validate
                )
                return self._action_result(
                    session_id, action_id, "activate", source, target_id=target_id
                )
            if descriptor.kind == "custom":
                session.grant.check("submit")
                source = self._live_pointer(
                    session_id, snapshot_id, target_id, validate
                )
                return self._action_result(
                    session_id, action_id, "activate", source, target_id=target_id
                )
        if arguments["operation"] == "enter":
            source = self.inspector.act(
                session_id, session.page, snapshot_id, target_id, arguments, validate
            )
        else:
            source = self._native_control(
                session_id, snapshot_id, target_id, arguments, validate
            )
        return self._action_result(
            session_id, action_id, arguments["operation"], source, target_id=target_id
        )

    def _live_drag(self, session_id, snapshot_id, target_id, to_target_id, validate):
        """Drag between two main-page targets measured twice at stable points."""
        session = self._session(session_id)
        if target_id == to_target_id:
            raise PageInspectionError("control_unsupported")
        requests = []
        for reference in (target_id, to_target_id):
            node_id, _ = self.inspector._target_reference(
                session_id, snapshot_id, reference
            )
            if self.inspector.route(session_id, reference) is not None:
                raise PageInspectionError("control_unsupported")
            requests.append(
                {"snapshot_id": snapshot_id, "node_id": node_id, "operation": "drag"}
            )
        if session.network is None:
            session.network = TaskNetwork(session.page, self, live=True)
        network = session.network
        result = Future()

        def measure(then):
            points = []

            def measured(source):
                if result.done():
                    return
                try:
                    points.append(source.result())
                    validate()
                    if len(points) < len(requests):
                        self.inspector.input_phase(
                            session_id,
                            session.page,
                            requests[len(points)],
                            "live_point",
                            validate,
                        ).add_done_callback(measured)
                    else:
                        then(points)
                except Exception as error:
                    result.set_exception(error)

            self.inspector.input_phase(
                session_id, session.page, requests[0], "live_point", validate
            ).add_done_callback(measured)

        def finish(source):
            if result.done():
                return
            try:
                source.result()
                result.set_result(
                    {
                        "status": "dispatched",
                        "changed": False,
                        "input_semantics": "chromium_pointer",
                    }
                )
            except Exception as error:
                result.set_exception(error)

        def confirmed(first, second):
            if first != second:
                raise PageInspectionError("target_moved")
            validate()
            start, end = ((p["x"], p["y"]) for p in second)
            network.input.drag(start, end, validate).add_done_callback(finish)

        def first_measure(first):
            QTimer.singleShot(
                60,
                lambda: (
                    result.done()
                    or measure(lambda second: guarded(confirmed, first, second))
                ),
            )

        def guarded(callback, *args):
            try:
                callback(*args)
            except Exception as error:
                if not result.done():
                    result.set_exception(error)

        def ready(source):
            if result.done():
                return
            try:
                source.result()
                validate()
                measure(first_measure)
            except Exception as error:
                result.set_exception(error)

        result.add_done_callback(
            lambda _: self.inspector.invalidate(session_id, "action_completed")
        )
        network.ready.add_done_callback(ready)
        return result

    def _live_pointer(self, session_id, snapshot_id, target_id, validate, hover=False):
        """Click (or hover) a target only at a verified, stable, unobscured point.

        A target inside a cross-origin frame is measured in its frame, then
        mapped through the frame element, which must be topmost on the page.
        """
        session = self._session(session_id)
        node_id, descriptor = self.inspector._target_reference(
            session_id, snapshot_id, target_id
        )
        route = self.inspector.route(session_id, target_id)
        if hover:
            if route is not None and route["path"].count("/") != 1:
                raise PageInspectionError("control_unsupported")
        elif (
            not descriptor.enabled
            or "activate" not in descriptor.operations
            or "pointer_activation" not in descriptor.constraints
        ):
            raise PageInspectionError("control_unsupported")
        if session.network is None:
            session.network = TaskNetwork(session.page, self, live=True)
        network = session.network
        result = Future()
        request = {"snapshot_id": snapshot_id, "node_id": node_id}
        if hover:
            request["operation"] = "hover"
        first = None

        def finish(source):
            if result.done():
                return
            try:
                source.result()
                result.set_result(
                    {
                        "status": "dispatched",
                        "changed": False,
                        "input_semantics": "chromium_pointer",
                    }
                )
            except Exception as error:
                result.set_exception(error)

        def located(future):
            """The target's point on the page, mapped through its frame."""
            if route is None:
                return future
            mapped = Future()

            def framed(source):
                try:
                    point = source.result()
                    validate()
                    self.inspector.input_phase(
                        session_id,
                        session.page,
                        {"snapshot_id": snapshot_id, "path": route["path"], **point},
                        "frame_point",
                        validate,
                    ).add_done_callback(
                        lambda page_point: (
                            mapped.set_exception(page_point.exception())
                            if page_point.exception()
                            else mapped.set_result(page_point.result())
                        )
                    )
                except Exception as error:
                    mapped.set_exception(error)

            future.add_done_callback(framed)
            return mapped

        def confirmed(source):
            if result.done():
                return
            try:
                point = source.result()
                # The page must not have moved the target between the checks.
                if point != first:
                    raise PageInspectionError("target_moved")
                validate()
                pointer = network.input.hover if hover else network.input.click
                pointer(point["x"], point["y"], validate).add_done_callback(finish)
            except Exception as error:
                result.set_exception(error)

        def measured(source):
            nonlocal first
            if result.done():
                return
            try:
                first = source.result()
                validate()
                QTimer.singleShot(
                    60,
                    lambda: (
                        result.done()
                        or located(
                            self.inspector.input_phase(
                                session_id,
                                session.page,
                                request,
                                "live_point",
                                validate,
                            )
                        ).add_done_callback(confirmed)
                    ),
                )
            except Exception as error:
                result.set_exception(error)

        def ready(source):
            if result.done():
                return
            try:
                source.result()
                validate()
                located(
                    self.inspector.input_phase(
                        session_id, session.page, request, "live_point", validate
                    )
                ).add_done_callback(measured)
            except Exception as error:
                result.set_exception(error)

        result.add_done_callback(
            lambda _: self.inspector.invalidate(session_id, "action_completed")
        )
        network.ready.add_done_callback(ready)
        return result

    def _live_activate(self, session_id, snapshot_id, target_id, operation, validate):
        """Bounded native activation; website effects require subsequent observation."""
        session = self._session(session_id)
        node_id, descriptor = self.inspector._target_reference(
            session_id, snapshot_id, target_id
        )
        if not descriptor.enabled or operation not in descriptor.operations:
            raise PageInspectionError("control_unsupported")
        if session.network is None:
            session.network = TaskNetwork(session.page, self, live=True)
        network = session.network
        result = Future()
        pending = None
        request = {"snapshot_id": snapshot_id, "node_id": node_id}

        def cleanup(_):
            if pending is not None and not pending.done():
                pending.set_exception(PageInspectionError("action_cancelled"))
            self.inspector.invalidate(session_id, "action_completed")

        def activated(source):
            if result.done():
                return
            try:
                source.result()
                result.set_result(
                    {
                        "status": "dispatched",
                        "changed": False,
                        "input_semantics": "chromium_keyboard",
                    }
                )
            except Exception as error:
                result.set_exception(error)

        def focused(source):
            nonlocal pending
            if result.done():
                return
            try:
                source.result()
                validate()
                pending = network.input.keys(["space"], validate, viewport=True)
                pending.add_done_callback(activated)
            except Exception as error:
                result.set_exception(error)

        def ready(source):
            nonlocal pending
            if result.done():
                return
            try:
                source.result()
                validate()
                pending = self.inspector.input_phase(
                    session_id, session.page, request, "live_prepare", validate
                )
                pending.add_done_callback(focused)
            except Exception as error:
                result.set_exception(error)

        result.add_done_callback(cleanup)
        network.ready.add_done_callback(ready)
        return result

    def _native_control(self, session_id, snapshot_id, target_id, arguments, validate):
        session = self._session(session_id)
        request = self.inspector.input_request(
            session_id, snapshot_id, target_id, arguments
        )
        if session.network is None:
            session.network = TaskNetwork(
                session.page, self, live=session.mode == "live"
            )
        network = session.network
        result = Future()
        deadline = QTimer(self)
        deadline.setSingleShot(True)
        before = None
        keys = []
        active = None
        settle = QTimer(self)
        settle.setSingleShot(True)
        observe_deadline = 0

        def cleanup(_):
            deadline.stop()
            deadline.deleteLater()
            settle.stop()
            settle.deleteLater()
            if active is not None and not active.done():
                active.set_exception(PageInspectionError("native_input_cancelled"))
            network.input.release_focus()
            self.inspector.invalidate(session_id, "action_completed")

        def expire():
            if not result.done():
                result.set_exception(PageInspectionError("native_input_timeout"))

        def observed(source):
            if result.done():
                return
            try:
                data = source.result()
                if request.get("adjust"):
                    # Custom widgets set their own value; report what it became.
                    changed = data["value_text"] != before
                    if not changed and time.monotonic() < observe_deadline:
                        settle.start(20)
                        return
                    result.set_result(
                        {
                            "value_text": data["value_text"],
                            "status": "applied" if changed else "no_change",
                            "changed": changed,
                            "input_semantics": "chromium_keyboard",
                        }
                    )
                    return
                key = (
                    "checked"
                    if arguments["operation"] == "activate"
                    else "selected_index"
                )
                changed = data[key] != before
                expected = (
                    (True if request.get("radio") else not before)
                    if key == "checked"
                    else arguments["index"]
                )
                if data[key] != expected and time.monotonic() < observe_deadline:
                    settle.start(20)
                    return
                status = "applied" if changed else "no_change"
                if data[key] != expected:
                    status = "unsupported"
                result.set_result(
                    {
                        **data,
                        "status": status,
                        "changed": changed,
                        "input_semantics": "chromium_keyboard",
                    }
                )
            except Exception as error:
                result.set_exception(error)

        def batch(index):
            nonlocal active, observe_deadline
            if result.done():
                return
            try:
                validate()
                network.validate()
                if index >= len(keys):
                    observe_deadline = time.monotonic() + 0.2
                    observe()
                else:
                    active = self.inspector.input_phase(
                        session_id, session.page, request, "input_check", validate
                    )
                    active.add_done_callback(lambda source: checked(source, index))
            except Exception as error:
                result.set_exception(error)

        def checked(source, index):
            nonlocal active
            if result.done():
                return
            try:
                source.result()
                validate()
                network.validate()
                active = network.input.keys(keys[index : index + 32], validate)
                active.add_done_callback(lambda source: advanced(source, index + 32))
            except Exception as error:
                result.set_exception(error)

        def observe():
            nonlocal active
            if not result.done():
                active = self.inspector.input_phase(
                    session_id, session.page, request, "input_observe", validate
                )
                active.add_done_callback(observed)

        def advanced(source, index):
            if result.done():
                return
            try:
                source.result()
                batch(index)
            except Exception as error:
                result.set_exception(error)

        def prepared(source):
            nonlocal before, keys
            if result.done():
                return
            try:
                data = source.result()
                before, keys = data["before"], data["keys"]
                request["radio"] = data.get("radio", False)
                request["adjust"] = data.get("adjust", False)
                batch(0)
            except Exception as error:
                result.set_exception(error)

        def ready(source):
            nonlocal active
            if result.done():
                return
            try:
                source.result()
                validate()
                network.validate()
                active = self.inspector.input_phase(
                    session_id, session.page, request, "input_layout", validate
                )
                active.add_done_callback(layout)
            except Exception as error:
                result.set_exception(error)

        def layout(source):
            nonlocal active
            if result.done():
                return
            try:
                data = source.result()
                active = network.input.keys(
                    [], validate, viewport=data["viewport_needed"]
                )
                active.add_done_callback(focused)
            except Exception as error:
                result.set_exception(error)

        def focused(source):
            nonlocal active
            if result.done():
                return
            try:
                source.result()
                validate()
                network.validate()
                active = self.inspector.input_phase(
                    session_id, session.page, request, "input_prepare", validate
                )
                active.add_done_callback(prepared)
            except Exception as error:
                result.set_exception(error)

        result.add_done_callback(cleanup)
        deadline.timeout.connect(expire)
        settle.timeout.connect(observe)
        deadline.start(self.input_timeout_ms)
        network.ready.add_done_callback(ready)
        return result

    def upload(self, session_id, action_id, snapshot_id, target_id, source):
        """Copy an approved source, bind it natively, and observe the same field."""
        if self._session(session_id).borrowed_view is not None:
            raise PageInspectionError("live_upload_unavailable")
        session, validate_read = self._edit_context(session_id, "upload")
        initial_url = session.page.url().toString()

        def validate():
            validate_read()
            session.grant.check(
                "upload", source=source, url=session.page.url().toString()
            )
            if webengine.option(session.page.settings(), "javascript"):
                raise PageInspectionError("scripted_upload_unavailable")

        validate()
        if len(session.uploads) >= 20:
            raise PageInspectionError("upload_limit")
        request = self.inspector.input_request(
            session_id, snapshot_id, target_id, {"operation": "upload"}
        )
        if session.network is None:
            session.network = TaskNetwork(
                session.page, self, live=session.mode == "live"
            )
        result = Future()
        copy = UploadCopy(
            source,
            session.grant,
            initial_url,
            session.page,
            remaining_bytes=session.grant.scope.max_upload_bytes
            - sum(c.size for c in session.uploads.values()),
        )
        session.pending_action = result
        upload_id = uuid4().hex
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(
            lambda: (
                None
                if result.done()
                else result.set_exception(PageInspectionError("upload_timeout"))
            )
        )
        timer.start(30000)
        retained = False

        def cleanup(_):
            timer.stop()
            timer.deleteLater()
            if session.pending_action is result:
                session.pending_action = None
            self.inspector.invalidate(session_id, "upload_completed")
            if not retained:
                copy.close()

        def observed(reply):
            nonlocal retained
            if result.done():
                return
            try:
                reply.result()
                validate()
                session.uploads[upload_id] = copy
                retained = True
                result.set_result(
                    FileUploadResult(
                        session_id,
                        action_id,
                        target_id,
                        upload_id,
                        "bound",
                        copy.path.name,
                        copy.size,
                        copy.digest,
                    )
                )
            except Exception as error:
                result.set_exception(error)

        def bound(reply):
            if result.done():
                return
            try:
                reply.result()
                validate()
                check = self.inspector.input_phase(
                    session_id,
                    session.page,
                    {
                        **request,
                        "upload_id": upload_id,
                        "name": copy.path.name,
                        "size": copy.size,
                    },
                    "upload_observe",
                    validate,
                )
                check.add_done_callback(observed)
            except Exception as error:
                result.set_exception(error)

        def focused(reply):
            if result.done():
                return
            try:
                reply.result()
                validate()
                session.network.validate()
                session.network.input.file(
                    copy.path, initial_url, validate
                ).add_done_callback(bound)
            except Exception as error:
                result.set_exception(error)

        def ready(_):
            if result.done():
                return
            try:
                copy.ready.result()
                validate()
                session.network.validate()
                if (
                    sum(c.size for c in session.uploads.values()) + copy.size
                    > session.grant.scope.max_upload_bytes
                ):
                    raise PageInspectionError("upload_storage_exhausted")
                self.inspector.input_phase(
                    session_id, session.page, request, "upload_prepare", validate
                ).add_done_callback(focused)
            except Exception as error:
                result.set_exception(error)

        def copied(_):
            if result.done():
                return
            try:
                copy.ready.result()
                session.network.ready.add_done_callback(ready)
            except Exception as error:
                result.set_exception(error)

        result.add_done_callback(cleanup)
        copy.ready.add_done_callback(copied)
        return result

    def scroll(
        self, session_id, action_id, snapshot_id, frame_id, arguments, target_id=None
    ):
        arguments = scroll_arguments(**arguments)
        session, validate = self._edit_context(session_id, "scroll")
        frame_id, source = self.inspector.scroll(
            session_id,
            session.page,
            snapshot_id,
            frame_id,
            arguments,
            validate,
            target_id=target_id,
        )
        reference = {"frame_id": frame_id}
        if target_id is not None:
            reference["target_id"] = target_id
        return self._action_result(session_id, action_id, "scroll", source, **reference)

    def submit_form(self, session_id, action_id, snapshot_id, target_id):
        """Authorize one native submission and observe its document load."""
        session, validate_edit = self._edit_context(session_id, "submit")
        if session.mode == "live":
            return self._submit_live(
                session_id, action_id, snapshot_id, target_id, validate_edit
            )

        def validate():
            validate_edit()
            # Script handlers run after DOM preflight and can alter sensitive
            # controls before native serialization. The one-use ticket binds
            # the route/method, not the request body. Keep this path scriptless.
            if webengine.option(session.page.settings(), "javascript"):
                raise PageInspectionError("scripted_submission_unavailable")

        validate()
        session.grant.check("navigate")
        if session.network is not None:
            session.network.validate()
        request = self.inspector.submission_request(session_id, snapshot_id, target_id)
        request["upload_available"] = "upload" in session.grant.scope.actions
        result = Future()
        session.pending_action = result
        ticket = session.page.submission
        timer = QTimer(self)
        timer.setSingleShot(True)
        dispatched = False
        previous_revision = session.revision

        def outcome(status):
            if result.done():
                return
            try:
                session.grant.check("submit")
                session.grant.check("read")
                if session.network is not None:
                    session.network.validate()
            except Exception as error:
                result.set_exception(error)
                return
            result.set_result(
                FormSubmissionResult(
                    session_id,
                    action_id,
                    target_id,
                    status,
                    session.revision,
                    journal_origin(session.page.url().toString()),
                    ticket.authorized(),
                )
            )

        def finished(ok):
            if dispatched and session.revision > previous_revision:
                if ticket.redirected:
                    outcome("redirect_not_followed")
                else:
                    outcome("loaded" if ok and ticket.authorized() else "load_failed")

        def expire():
            if result.done():
                return
            outcome("timed_out")
            self.stop(session_id)

        def cleanup(_):
            ticket.clear()
            timer.stop()
            timer.deleteLater()
            session.page.loadFinished.disconnect(finished)
            if session.pending_action is result:
                session.pending_action = None
            self.inspector.invalidate(session_id, "submission_completed")

        def received(raw):
            if result.done():
                return
            try:
                session.grant.check("submit")
                if not isinstance(raw, str) or len(raw) > 4096:
                    raise PageInspectionError("submission_outcome_unknown")
                data = json.loads(raw)
                if "error" in data:
                    raise PageInspectionError(data["error"])
                if data.get("status") == "validation_failed":
                    outcome("validation_failed")
                elif data.get("status") != "submitted":
                    raise PageInspectionError("submission_outcome_unknown")
            except Exception as error:
                result.set_exception(error)

        def prepared(source):
            nonlocal dispatched
            if result.done():
                return
            try:
                expected = source.result()
                validate()
                if expected.get("file_form"):
                    session.grant.check("upload", url=session.page.url().toString())
                    session.grant.check("upload", url=expected["url"])
                    for file in expected.get("uploads", []):
                        copy = session.uploads.get(file["upload_id"])
                        if (
                            copy is None
                            or file["name"] != copy.path.name
                            or file["size"] != copy.size
                        ):
                            raise PageInspectionError("upload_not_owned")
                        copy.authority()
                        session.grant.check(
                            "upload", source=copy.source, url=expected["url"]
                        )
                if session.network is not None:
                    session.network.validate()
                ticket.arm(expected["url"], expected["method"])
                dispatched = True
                timer.start(self.navigation_timeout_ms)
                self.inspector.dispatch_submission(
                    session.page, request, expected, received
                )
            except Exception as error:
                result.set_exception(error)

        session.page.loadFinished.connect(finished)
        timer.timeout.connect(expire)
        result.add_done_callback(cleanup)
        source = self.inspector.prepare_submission(
            session_id, session.page, request, validate
        )
        source.add_done_callback(prepared)
        return result

    def _submit_live(self, session_id, action_id, snapshot_id, target_id, validate):
        session = self._session(session_id)
        session.grant.check("navigate")
        request = self.inspector.submission_request(session_id, snapshot_id, target_id)
        result = Future()
        session.pending_action = result
        pending = None

        def cleanup(_):
            if session.pending_action is result:
                session.pending_action = None
            if pending is not None and not pending.done():
                pending.set_exception(PageInspectionError("action_cancelled"))

        def activated(source):
            if result.done():
                return
            try:
                source.result()
                result.set_result(
                    FormSubmissionResult(
                        session_id,
                        action_id,
                        target_id,
                        "dispatched",
                        session.revision,
                        journal_origin(session.page.url().toString()),
                        False,
                    )
                )
            except Exception as error:
                result.set_exception(error)

        def prepared(source):
            nonlocal pending
            if result.done():
                return
            try:
                expected = source.result()
                validate()
                session.grant.check("submit", url=expected["url"])
                session.grant.check("navigate", url=expected["url"])
                pending = self._live_activate(
                    session_id, snapshot_id, target_id, "submit", validate
                )
                pending.add_done_callback(activated)
            except Exception as error:
                result.set_exception(error)

        result.add_done_callback(cleanup)
        pending = self.inspector.prepare_submission(
            session_id, session.page, request, validate
        )
        pending.add_done_callback(prepared)
        return result

    def _action_result(self, session_id, action_id, operation, source, **reference):
        session = self._session(session_id)
        result = Future()
        session.pending_action = result

        def finished(source):
            if result.done():
                return
            try:
                data = source.result()
                for key in ("scroll_position", "container_size"):
                    if data.get(key) is not None:
                        data[key] = tuple(data[key])
                result.set_result(
                    PageActionResult(
                        session_id,
                        action_id,
                        operation,
                        revision=session.revision,
                        **reference,
                        **data,
                    )
                )
            except Exception as error:
                result.set_exception(error)

        def cleanup(_):
            if session.pending_action is result:
                session.pending_action = None
            if not source.done():
                source.set_exception(PageInspectionError("action_cancelled"))

        result.add_done_callback(cleanup)
        source.add_done_callback(finished)
        return result

    def wait(self, session_id, condition, text=None, timeout_ms=5000):
        wait_arguments(condition, text, timeout_ms)
        session = self._session(session_id)
        session.grant.check("read")
        if session.pending_action is not None or session.waits:
            raise PageInspectionError("action_in_progress")
        if condition in ("text", "settled", "changed") and (
            session.loading or session.load_success is not True
        ):
            raise PageInspectionError("page_not_ready")
        result = Future()
        session.waits.add(result)
        poll = QTimer(self)
        poll.setSingleShot(True)
        deadline = QTimer(self)
        deadline.setSingleShot(True)
        revision = session.revision
        limitations = (
            ("inspection_not_completed",)
            if condition in ("text", "settled", "changed")
            else ()
        )
        reading = None
        # "settled": text, controls and size unchanged for SETTLE_MS of reads.
        # "changed": the baseline is the agent's last inspection of this page
        # revision, or else the first read; any later difference matches.
        signature = None
        if condition == "changed":
            observed = self.inspector.observed.get(session_id)
            if observed is not None and observed[0] == revision:
                signature = observed[1]
        changed_at = time.monotonic()

        def cleanup(_):
            session.waits.discard(result)
            poll.stop()
            deadline.stop()
            poll.deleteLater()
            deadline.deleteLater()
            if reading is not None and not reading.done():
                self.inspector.invalidate(session_id, "wait_finished")

        def finish(status):
            if result.done():
                return
            metadata = self.inspect(session_id)
            result.set_result(
                PageWaitResult(
                    session_id,
                    condition,
                    status,
                    metadata.url,
                    metadata.revision,
                    limitations,
                )
            )

        def inspected(source):
            nonlocal limitations, signature, changed_at
            if result.done():
                return
            try:
                snapshot = source.result()
                limitations = snapshot.limitations
                if condition == "settled":
                    current = tuple(
                        (
                            len(frame.text),
                            len(frame.targets),
                            frame.scroll.height if frame.scroll else 0,
                        )
                        for frame in snapshot.frames
                    )
                    now = time.monotonic()
                    if current != signature:
                        signature, changed_at = current, now
                    elif now - changed_at >= SETTLE_MS / 1000:
                        finish("matched")
                        return
                    poll.start(250)
                elif condition == "changed":
                    current = content_signature(snapshot)
                    if signature is None:
                        signature = current
                    elif current != signature:
                        finish("matched")
                        return
                    poll.start(150)
                elif any(text in frame.text for frame in snapshot.frames):
                    finish("matched")
                else:
                    poll.start(100)
            except Exception as error:
                result.set_exception(error)

        def tick():
            nonlocal reading
            if result.done():
                return
            try:
                self._session(session_id)
                session.grant.check(
                    "read", url=session.page.url().toString() or "about:blank"
                )
                if condition == "ready":
                    if not session.loading and session.load_success is not None:
                        finish("matched" if session.load_success else "load_failed")
                    else:
                        poll.start(50)
                else:
                    if session.revision != revision:
                        if condition == "changed":
                            finish("matched")
                            return
                        raise PageInspectionError("stale_snapshot")
                    reading = self.inspect_page(session_id)
                    reading.add_done_callback(inspected)
            except Exception as error:
                result.set_exception(error)

        poll.timeout.connect(tick)
        deadline.timeout.connect(lambda: finish("timed_out"))
        result.add_done_callback(cleanup)
        deadline.start(timeout_ms)
        tick()
        return result

    def attach_view(self, session_id, parent=None):
        session = self._session(session_id)
        if session.borrowed_view is not None:
            return session.borrowed_view
        session.grant.check("attach")
        if session.pending_action is not None:
            raise PageInspectionError("session_busy")
        session.views = [view for view in session.views if not sip.isdeleted(view)]
        if session.views:
            return session.views[0]
        view = QWebEngineView(parent)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        view.setPage(session.page)
        from ui.task_watch import InputShield

        view.input_shield = InputShield(view)
        self.inspector.invalidate(session_id, "view_attached")
        session.revision += 1
        session.views.append(view)
        return view

    def check_control_change(self, session_id):
        session = self._session(session_id)
        if (
            session.loading
            or session.pending_action is not None
            or session.pending_navigation is not None
        ):
            raise PageInspectionError("session_busy")

    def start_download(self, session_id, download_id, url, destination, publish):
        self.check_control_change(session_id)
        session = self._session(session_id)
        if session.borrowed_view is not None:
            raise PageInspectionError("live_download_unavailable")
        if len(session.downloads) >= 100:
            raise PageInspectionError("download_limit")
        session.grant.check("download", url=url, destination=destination)
        transfer = TaskDownload(
            session_id,
            download_id,
            session.page,
            session.grant,
            url,
            destination,
            publish,
        )
        session.downloads[download_id] = transfer
        session.pending_action = transfer.result

        def completed(_):
            if session.pending_action is transfer.result:
                session.pending_action = None
            session.page.loadFinished.disconnect(loaded)

        def loaded(ok):
            def checked():
                # Attachment navigation reports loadFinished(False) before
                # downloadRequested. Wait for the explicit transfer deadline
                # in that case rather than racing the engine's queued request.
                if ok and transfer.request is None and not transfer.result.done():
                    transfer.cancel("not_a_download")

            QTimer.singleShot(0, checked)

        session.page.loadFinished.connect(loaded)
        transfer.result.add_done_callback(completed)

        def navigate():
            if transfer.result.done() or session_id not in self._sessions:
                return
            try:
                if session.network is not None:
                    session.network.validate()
                session.grant.check("download", url=url, destination=destination)
                session.page.setUrl(navigation_url(url))
            except Exception:
                transfer.cancel("download_navigation_failed")

        if session.network is not None and not session.network.ready.done():
            session.network.ready.add_done_callback(lambda _: navigate())
        else:
            navigate()
        return transfer

    def _download_requested(self, session_id, request):
        session = self._sessions.get(session_id)
        if session is None:
            request.cancel()
            return
        pending = next(
            (t for t in session.downloads.values() if not t.result.done()), None
        )
        if pending is None:
            request.cancel()
        else:
            pending.accept(request)

    def inspect_download(self, session_id, download_id):
        return self._session(session_id).downloads[download_id].snapshot()

    def list_downloads(self, session_id):
        return [t.snapshot() for t in self._session(session_id).downloads.values()]

    def cancel_download(self, session_id, download_id):
        transfer = self._session(session_id).downloads[download_id]
        transfer.cancel()
        return transfer.snapshot()

    def change_control(self, session_id, owner):
        self.check_control_change(session_id)
        session = self._session(session_id)
        self._cancel_waits(session, "control_changed")
        self.inspector.invalidate(session_id, "control_changed")
        session.revision += 1
        session.control_owner = owner
        if session.mode == "live":
            for view in session.views:
                if not sip.isdeleted(view):
                    view.input_shield.setEnabled(owner == "agent")
        return self.inspect(session_id)

    def close_session(self, session_id):
        session = self._session(session_id)
        for transfer in session.downloads.values():
            transfer.close()
        for copy in session.uploads.values():
            copy.close()
        self.inspector.invalidate(session_id, "session_closed", discard=True)
        self._sessions.pop(session_id)
        self._cancel_navigation(session)
        self._cancel_action(session, "session_closed")
        self._cancel_waits(session, "session_closed")
        for view in session.views:
            if not sip.isdeleted(view):
                sip.delete(view)
        if session.borrowed_view is not None:
            for signal, callback in session.connections:
                try:
                    signal.disconnect(callback)
                except (TypeError, RuntimeError):
                    pass
            if session.shield is not None and not sip.isdeleted(session.shield):
                sip.delete(session.shield)
            if not sip.isdeleted(session.borrowed_view):
                session.borrowed_view.setFocusPolicy(session.focus_policy)
            if session.network is not None:
                session.network.close()
                sip.delete(session.network)
            return
        session.page.blockSignals(True)
        self.navigation.action(session.page, "stop")
        if session.network is not None:
            session.network.close()
            sip.delete(session.network)
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
