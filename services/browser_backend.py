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
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QWidget

from services.command_broker import require_owner_thread
from services.engine_capabilities import qt_engine_capabilities
from services.form_submission import SubmissionTicket, journal_origin
from services.navigation_service import NavigationService
from services.page_actions import (
    FormSubmissionResult,
    PageActionResult,
    PageWaitResult,
    action_arguments,
    scroll_arguments,
    wait_arguments,
)
from services.page_inspector import PageInspectionError, PageInspector
from services.permission_service import PermissionService
from services.task_downloads import TaskDownload
from services.task_network import TaskNetwork
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
    control_owner: str = "agent"


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
    def inspect_target(self, session_id: str, snapshot_id: str, target_id: str): ...
    def navigate_observed(self, session_id: str, url: str): ...
    def act(self, session_id, action_id, snapshot_id, target_id, arguments): ...
    def submit_form(self, session_id, action_id, snapshot_id, target_id): ...
    def scroll(self, session_id, action_id, snapshot_id, frame_id, arguments): ...
    def wait(self, session_id, condition, text, timeout_ms): ...
    def start_download(self, session_id, download_id, url, destination, publish): ...
    def inspect_download(self, session_id, download_id): ...
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


class TaskPage(QWebEnginePage):
    """Suppress dialogs, uploads, and unsupported main-document schemes."""

    def __init__(self, profile, parent, grant, blocked):
        super().__init__(profile, parent)
        self.grant = grant
        self.report_blocked = blocked
        self.is_task_page = True
        self.network_ready = True
        self.submission = SubmissionTicket(grant)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if not self.network_ready and url.toString() != "about:blank":
            return False
        reason = self.grant.navigation(url.toString())
        if (
            not reason
            and navigation_type == self.NavigationType.NavigationTypeFormSubmitted
        ):
            reason = self.submission.navigation(url.toString(), is_main_frame)
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
    page: TaskPage
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

        page = TaskPage(profile, self, grant, report)
        if owns_profile:
            interceptor = TaskRequestInterceptor(
                grant, report, profile, submission=page.submission
            )
            profile.setUrlRequestInterceptor(interceptor)
            profile.task_interceptor = interceptor
            profile.downloadRequested.connect(
                lambda request: page.task_download_handler(request)
            )
        if not owns_profile:
            interceptor = TaskRequestInterceptor(
                grant, report, page, submission=page.submission
            )
            page.setUrlRequestInterceptor(interceptor)
            page.task_interceptor = interceptor
            page.network_ready = False
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
        page.task_download_handler = lambda request: self._download_requested(
            session_id, request
        )
        page.loadStarted.connect(lambda: self._started(session_id))
        page.loadFinished.connect(lambda ok: self._finished(session_id, ok))
        page.urlChanged.connect(lambda url: self._url_changed(session_id))
        page.titleChanged.connect(lambda title: self._updated(session_id))
        page.renderProcessTerminated.connect(lambda *_: self._terminated(session_id))
        if not owns_profile:
            page.setUrl(QUrl("about:blank"))
            session.network = TaskNetwork(page, self)
        return self.inspect(session_id)

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

    def _native_control(self, session_id, snapshot_id, target_id, arguments, validate):
        session = self._session(session_id)
        request = self.inspector.input_request(
            session_id, snapshot_id, target_id, arguments
        )
        if session.network is None:
            session.network = TaskNetwork(session.page, self)
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

    def scroll(self, session_id, action_id, snapshot_id, frame_id, arguments):
        arguments = scroll_arguments(**arguments)
        session, validate = self._edit_context(session_id, "scroll")
        frame_id, source = self.inspector.scroll(
            session_id, session.page, snapshot_id, frame_id, arguments, validate
        )
        return self._action_result(
            session_id, action_id, "scroll", source, frame_id=frame_id
        )

    def submit_form(self, session_id, action_id, snapshot_id, target_id):
        """Authorize one native submission and observe its document load."""
        session, validate_edit = self._edit_context(session_id, "submit")

        def validate():
            validate_edit()
            # Script handlers run after DOM preflight and can alter sensitive
            # controls before native serialization. The one-use ticket binds
            # the route/method, not the request body. Keep this path scriptless.
            if session.page.settings().testAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled
            ):
                raise PageInspectionError("scripted_submission_unavailable")

        validate()
        session.grant.check("navigate")
        if session.network is not None:
            session.network.validate()
        request = self.inspector.submission_request(session_id, snapshot_id, target_id)
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

    def _action_result(self, session_id, action_id, operation, source, **reference):
        session = self._session(session_id)
        result = Future()
        session.pending_action = result

        def finished(source):
            if result.done():
                return
            try:
                data = source.result()
                position = data.get("scroll_position")
                if position is not None:
                    data["scroll_position"] = tuple(position)
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
        if condition == "text" and (
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
        limitations = ()
        reading = None

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
            nonlocal limitations
            if result.done():
                return
            try:
                snapshot = source.result()
                limitations = snapshot.limitations
                if any(text in frame.text for frame in snapshot.frames):
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
        return self.inspect(session_id)

    def close_session(self, session_id):
        session = self._session(session_id)
        for transfer in session.downloads.values():
            transfer.close()
        self.inspector.invalidate(session_id, "session_closed", discard=True)
        self._sessions.pop(session_id)
        self._cancel_navigation(session)
        self._cancel_action(session, "session_closed")
        self._cancel_waits(session, "session_closed")
        for view in session.views:
            if not sip.isdeleted(view):
                sip.delete(view)
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
