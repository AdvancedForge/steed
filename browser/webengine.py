"""Steed's web engine: the one module that talks to Qt WebEngine.

Everything else asks for things in Steed's terms, so another engine means
another version of this module, not changes across the browser. What an
engine provides (tests/test_engine_boundary.py keeps everything else from
reaching past it):

- Page, View and Profile: a page, the widget showing it, and the storage it
  belongs to. Steed's pages subclass Page and decide through Steed's hooks
  (navigation_allowed, new_window, console_message, choose_files and the
  script_alert/confirm/prompt dialogs), with kinds named in words
  ("link", "form", "background_tab"...), never the engine's enums. Their
  everyday methods (url, title, setUrl, load, back, history, zoom, the load
  and title signals) are the common ground of web views and part of the
  interface as they are.
- Request and RequestFilter: every network request a profile or page makes,
  with its address, page, initiator, method and kind, to block, redirect or
  add a header to.
- SchemeHandler and SchemeRequest: Steed's own steed:// pages.
- Scripts in two worlds (PAGE_WORLD, STEED_WORLD): run_script, page_script.
- Frames, cookies, developer tools, permissions, downloads, saving pages,
  page actions, find, tab sleep states and crash reasons, as functions here.
"""

import os
import re
from pathlib import Path

from PyQt6 import QtWebEngineCore
from PyQt6.QtCore import QBuffer, QIODevice, QUrl
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

Profile = QWebEngineProfile
# The bridge between Steed's own pages and Python (a Qt WebChannel).
Channel = QWebChannel
# Passkey and security-key prompts (ui.web_auth); None where unavailable.
WebAuthRequest = getattr(QtWebEngineCore, "QWebEngineWebAuthUxRequest", None)

# ---- Pages and views, decided through Steed's hooks ----------------------

_NAV = QWebEnginePage.NavigationType
NAVIGATIONS = {
    _NAV.NavigationTypeLinkClicked: "link",
    _NAV.NavigationTypeTyped: "typed",
    _NAV.NavigationTypeFormSubmitted: "form",
    _NAV.NavigationTypeBackForward: "back_forward",
    _NAV.NavigationTypeReload: "reload",
    _NAV.NavigationTypeRedirect: "redirect",
    _NAV.NavigationTypeOther: "other",
}
# Requests carry their own navigation enum, with "Link" for "LinkClicked".
_REQUEST_NAV = QWebEngineUrlRequestInfo.NavigationType
REQUEST_NAVIGATIONS = {
    _REQUEST_NAV.NavigationTypeLink: "link",
    _REQUEST_NAV.NavigationTypeTyped: "typed",
    _REQUEST_NAV.NavigationTypeFormSubmitted: "form",
    _REQUEST_NAV.NavigationTypeBackForward: "back_forward",
    _REQUEST_NAV.NavigationTypeReload: "reload",
    _REQUEST_NAV.NavigationTypeRedirect: "redirect",
    _REQUEST_NAV.NavigationTypeOther: "other",
}
_WINDOW = QWebEnginePage.WebWindowType
WINDOWS = {
    _WINDOW.WebBrowserWindow: "window",
    _WINDOW.WebBrowserTab: "tab",
    _WINDOW.WebDialog: "dialog",
    _WINDOW.WebBrowserBackgroundTab: "background_tab",
}
_LEVEL = QWebEnginePage.JavaScriptConsoleMessageLevel
CONSOLE_LEVELS = {
    _LEVEL.InfoMessageLevel: "info",
    _LEVEL.WarningMessageLevel: "warning",
    _LEVEL.ErrorMessageLevel: "error",
}


class Page(QWebEnginePage):
    """A web page. Subclasses override the Steed hooks below."""

    def acceptNavigationRequest(self, url, kind, main_frame):
        return self.navigation_allowed(url, NAVIGATIONS.get(kind, "other"), main_frame)

    def navigation_allowed(self, url, kind, main_frame) -> bool:
        """kind: link, typed, form, back_forward, reload, redirect or other."""
        return True

    def createWindow(self, kind):
        return self.new_window(WINDOWS.get(kind, "tab"))

    def new_window(self, kind):
        """A page asks for a window, tab, background_tab or dialog: return a
        Page to load it in, or None to refuse."""
        return None

    def javaScriptConsoleMessage(self, level, message, line, source):
        self.console_message(CONSOLE_LEVELS.get(level, "info"), message, line, source)

    def console_message(self, level, message, line, source):
        """level: info, warning or error. By default the engine prints it."""
        engine_level = next(k for k, v in CONSOLE_LEVELS.items() if v == level)
        QWebEnginePage.javaScriptConsoleMessage(
            self, engine_level, message, line, source
        )

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        several = mode == QWebEnginePage.FileSelectionMode.FileSelectOpenMultiple
        return self.choose_files(several, old_files, accepted_mime_types)

    def choose_files(self, several, old_files, accepted_types):
        """Files for an upload field (several: it takes more than one)."""
        mode = QWebEnginePage.FileSelectionMode
        return QWebEnginePage.chooseFiles(
            self,
            mode.FileSelectOpenMultiple if several else mode.FileSelectOpen,
            old_files,
            accepted_types,
        )

    def javaScriptAlert(self, origin, message):
        self.script_alert(origin, message)

    def script_alert(self, origin, message):
        QWebEnginePage.javaScriptAlert(self, origin, message)

    def javaScriptConfirm(self, origin, message):
        return self.script_confirm(origin, message)

    def script_confirm(self, origin, message) -> bool:
        return QWebEnginePage.javaScriptConfirm(self, origin, message)

    def javaScriptPrompt(self, origin, message, default):
        return self.script_prompt(origin, message, default)

    def script_prompt(self, origin, message, default):
        return QWebEnginePage.javaScriptPrompt(self, origin, message, default)


class View(QWebEngineView):
    """The widget showing a page. Subclasses override new_window."""

    def createWindow(self, kind):
        return self.new_window(WINDOWS.get(kind, "tab"))

    def new_window(self, kind):
        """Return a View for the new window, tab, background_tab or dialog."""
        return None


def is_view(widget) -> bool:
    return isinstance(widget, QWebEngineView)


def is_page(value) -> bool:
    return isinstance(value, QWebEnginePage)


# ---- Network requests ------------------------------------------------------

# Request kinds in the words browser extensions use (declarativeNetRequest).
_RESOURCES = {
    "mainframe": "main_frame",
    "navigationpreloadmainframe": "main_frame",
    "subframe": "sub_frame",
    "navigationpreloadsubframe": "sub_frame",
    "stylesheet": "stylesheet",
    "script": "script",
    "image": "image",
    "favicon": "image",
    "fontresource": "font",
    "object": "object",
    "pluginresource": "object",
    "xhr": "xmlhttprequest",
    "ping": "ping",
    "cspreport": "csp_report",
    "media": "media",
    "websocket": "websocket",
    "worker": "worker",
    "sharedworker": "shared_worker",
    "serviceworker": "service_worker",
    "prefetch": "prefetch",
}


class Request:
    """One network request, in Steed's terms. resource is its kind, as browser
    extensions name them: main_frame, sub_frame, script, stylesheet, image,
    font, object, xmlhttprequest, media, websocket, ping, csp_report, worker,
    shared_worker, service_worker, prefetch or other."""

    __slots__ = ("_info",)

    def __init__(self, info):
        self._info = info

    @property
    def url(self) -> QUrl:
        return self._info.requestUrl()

    @property
    def page(self) -> QUrl:
        """The page the request is for (its first party)."""
        return self._info.firstPartyUrl()

    @property
    def initiator(self) -> QUrl:
        return self._info.initiator()

    @property
    def method(self) -> bytes:
        return bytes(self._info.requestMethod())

    @property
    def resource(self) -> str:
        kind = self._info.resourceType().name.removeprefix("ResourceType").lower()
        return _RESOURCES.get(kind, "other")

    @property
    def navigation(self) -> str:
        return REQUEST_NAVIGATIONS.get(self._info.navigationType(), "other")

    @property
    def main_frame(self) -> bool:
        return (
            self._info.resourceType()
            == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame
        )

    def block(self, blocked: bool = True) -> None:
        self._info.block(blocked)

    def redirect(self, url: QUrl) -> None:
        self._info.redirect(url)

    def set_header(self, name: bytes, value: bytes) -> None:
        self._info.setHttpHeader(name, value)


class RequestFilter(QWebEngineUrlRequestInterceptor):
    """Sees every request of the profile or page it is set on (filter())."""

    def interceptRequest(self, info):
        self.filter(Request(info))

    def filter(self, request: Request) -> None:
        """Block, redirect or add headers to a request; anything else lets it go."""


def set_request_filter(target, request_filter) -> None:
    """Filter a profile's (or one page's) requests; None removes it."""
    target.setUrlRequestInterceptor(request_filter)


# ---- Steed's own pages (steed://) ----------------------------------------


def register_scheme(name: bytes) -> None:
    """A secure, local scheme whose host names a page; register before the
    application starts. Its pages may fetch() their own address."""
    if QWebEngineUrlScheme.schemeByName(name).name():
        return
    scheme = QWebEngineUrlScheme(name)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalScheme
        | QWebEngineUrlScheme.Flag.FetchApiAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)


class SchemeRequest:
    """A request for one of Steed's own pages, to answer or refuse."""

    __slots__ = ("_job",)

    def __init__(self, job):
        self._job = job

    @property
    def url(self) -> QUrl:
        return self._job.requestUrl()

    @property
    def method(self) -> bytes:
        return bytes(self._job.requestMethod())

    @property
    def initiator(self) -> QUrl:
        return self._job.initiator()

    @property
    def alive(self) -> bool:
        """False once the page that asked has gone (answer nothing then)."""
        from PyQt6 import sip

        return not sip.isdeleted(self._job)

    def reply(self, mime: bytes, data: bytes) -> None:
        buffer = QBuffer(self._job)
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._job.reply(mime, buffer)

    def fail(self, reason: str) -> None:
        """reason: denied, not_found, invalid or failed."""
        fail_request(self._job, reason)


class SchemeHandler(QWebEngineUrlSchemeHandler):
    def requestStarted(self, job):
        self.handle(SchemeRequest(job))

    def handle(self, request: SchemeRequest) -> None:
        request.fail("not_found")


def install_scheme(profile, name: bytes, handler) -> None:
    profile.installUrlSchemeHandler(name, handler)


# ---- Frames, cookies, developer tools, permissions, downloads -----------


def frames(page):
    """Every frame of a page, main frame first."""
    found, pending = [], [page.mainFrame()]
    while pending:
        frame = pending.pop(0)
        found.append(frame)
        pending.extend(frame.children())
    return found


def main_frame(page):
    return page.mainFrame()


def cookie_store(profile):
    return profile.cookieStore()


def devtools_of(page):
    """The developer tools page attached to a page, or None."""
    return page.devToolsPage()


def attach_devtools(page, tools) -> None:
    """Attach (or with None, detach) a developer tools page."""
    page.setDevToolsPage(tools)


# Newer engines hand permissions over as an object; older ones as a feature
# enum on the page (Qt before 6.8).
_PERMISSION = getattr(QtWebEngineCore, "QWebEnginePermission", None)
# The engine's permission request type, for tests and type checks (or None).
Permission = _PERMISSION
MODERN_PERMISSIONS = _PERMISSION is not None


def on_permission(page, modern, legacy, *, modern_available=None) -> None:
    """modern(permission) or legacy(page, origin, feature) for each request."""
    if modern_available is None:
        modern_available = MODERN_PERMISSIONS
    if modern_available and hasattr(page, "permissionRequested"):
        page.permissionRequested.connect(modern)
    else:
        page.featurePermissionRequested.connect(
            lambda origin, feature: legacy(page, origin, feature)
        )


def keep_permission(permission):
    """A copy of a permission request that outlives the signal delivering it."""
    return _PERMISSION(permission)


def answer_legacy_permission(page, origin, feature, allowed: bool) -> None:
    policy = QWebEnginePage.PermissionPolicy
    page.setFeaturePermission(
        origin,
        feature,
        policy.PermissionGrantedByUser if allowed else policy.PermissionDeniedByUser,
    )


def on_download(profile, handler) -> None:
    """handler(download) for each download the profile starts."""
    profile.downloadRequested.connect(handler)


_SAVE_FORMATS = {
    "single": QWebEngineDownloadRequest.SavePageFormat.SingleHtmlSaveFormat,
    "complete": QWebEngineDownloadRequest.SavePageFormat.CompleteHtmlSaveFormat,
    "mhtml": QWebEngineDownloadRequest.SavePageFormat.MimeHtmlSaveFormat,
}


def save_page(page, path: str, kind: str) -> None:
    """Save a page as "single" HTML, "complete" (with a folder) or "mhtml"."""
    page.save(path, _SAVE_FORMATS[kind])


_ENDED = QWebEnginePage.RenderProcessTerminationStatus
CRASHES = {
    _ENDED.NormalTerminationStatus: "normal",
    _ENDED.AbnormalTerminationStatus: "abnormal",
    _ENDED.CrashedTerminationStatus: "crashed",
    _ENDED.KilledTerminationStatus: "killed",
}


def crash_reason(status) -> str:
    """Why a page's process ended: normal, abnormal, crashed or killed."""
    return CRASHES.get(status, "abnormal")


# qutebrowser uses this site-specific workaround for Google sign-in:
# https://github.com/qutebrowser/qutebrowser/issues/5182
# Keep it restricted to the account host; Firefox identification elsewhere can
# cause sites to select features that Chromium does not implement.
GOOGLE_LOGIN_USER_AGENT = (
    b"Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0"
)


# Chromium and Qt WebEngine add these variables from engine threads when they
# are missing (Chromium's VA-API setup on a worker thread). Adding a variable can
# reallocate the process environment while another engine thread is reading it
# in getenv() (fontconfig startup), which crashes the browser. Defining them
# before Qt starts any thread leaves later writes nothing to add.
ENGINE_ENVIRONMENT = {"LIBVA_MESSAGING_LEVEL": "1", "force_s3tc_enable": "true"}


FONTS = Path(__file__).resolve().parents[1] / "ui" / "assets" / "fonts"
UI_FONT = "Mona Sans"


def prepare_engine_environment() -> None:
    """Call before creating QApplication; keeps any value the user set."""
    for name, value in ENGINE_ENVIRONMENT.items():
        os.environ.setdefault(name, value)
    # Steed's typeface for Qt and for pages, without installing it: the
    # system's font setup plus the bundled folder.
    if (FONTS / "fonts.conf").is_file():
        os.environ.setdefault("FONTCONFIG_FILE", str(FONTS / "fonts.conf"))
    # Spell check dictionaries (services.spellcheck); read when the engine starts.
    from services import spellcheck

    try:
        spellcheck.folder().mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    os.environ.setdefault("QTWEBENGINE_DICTIONARIES_PATH", str(spellcheck.folder()))


def google_login_user_agent(url: QUrl) -> bytes | None:
    """Return the compatibility header for the HTTPS Google account host."""
    if url.scheme() == "https" and url.host() == "accounts.google.com":
        return GOOGLE_LOGIN_USER_AGENT
    return None


class SiteCompatibilityInterceptor(RequestFilter):
    """Apply account-host compatibility after profile-level ad filtering."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.last_main_method = b""
        self.last_main_url = QUrl()
        # (https address, the http one asked for) of the last HTTPS-first load.
        self.upgraded = None

    @property
    def enabled(self):
        return self.config is not None and bool(
            self.config.get("google_login_compatibility", True)
        )

    def filter(self, request):
        url = request.url
        if request.main_frame:
            self.last_main_method = request.method
            self.last_main_url = url
            if self.last_main_method == b"GET":
                from browser import privacy

                asked = url.toString()
                target = privacy.navigation(asked, self.config)
                if target != asked:
                    if target.startswith("https:") and asked.startswith("http:"):
                        self.upgraded = (target, asked)
                    request.redirect(QUrl(target))
                    return
        if self.enabled:
            user_agent = google_login_user_agent(url)
            if user_agent is not None:
                request.set_header(b"User-Agent", user_agent)


# Page options by Steed name; set_options(settings, javascript=False, ...).
_WEB = QWebEngineSettings.WebAttribute
_OPTIONS = {
    "javascript": _WEB.JavascriptEnabled,
    "scripts_open_windows": _WEB.JavascriptCanOpenWindows,
    "scripts_use_clipboard": _WEB.JavascriptCanAccessClipboard,
    "scripts_paste": _WEB.JavascriptCanPaste,
    "local_files_read_files": _WEB.LocalContentCanAccessFileUrls,
    "local_files_reach_web": _WEB.LocalContentCanAccessRemoteUrls,
    "insecure_content": _WEB.AllowRunningInsecureContent,
    "screen_capture": _WEB.ScreenCaptureEnabled,
    "dns_prefetch": _WEB.DnsPrefetchEnabled,
    "hyperlink_auditing": _WEB.HyperlinkAuditingEnabled,
    "plugins": _WEB.PluginsEnabled,
    "pdf_viewer": _WEB.PdfViewerEnabled,
    "fullscreen": _WEB.FullScreenSupportEnabled,
    "webgl": _WEB.WebGLEnabled,
    "canvas_acceleration": _WEB.Accelerated2dCanvasEnabled,
    "images": _WEB.AutoLoadImages,
    "media_needs_gesture": _WEB.PlaybackRequiresUserGesture,
    "dark_pages": _WEB.ForceDarkMode,
}
PAGE_OPTIONS = tuple(_OPTIONS)


def set_options(settings, **values: bool) -> None:
    for name, value in values.items():
        settings.setAttribute(_OPTIONS[name], bool(value))


def option(settings, name: str) -> bool:
    return settings.testAttribute(_OPTIONS[name])


def block_unknown_schemes(settings) -> None:
    """Links to schemes with no handler (mailto:, apps) do nothing."""
    settings.setUnknownUrlSchemePolicy(
        QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes
    )


def configure_web_settings(settings: QWebEngineSettings) -> None:
    """Apply the browser's existing media and rendering preferences."""
    set_options(
        settings,
        plugins=True,
        pdf_viewer=True,
        fullscreen=True,
        webgl=True,
        canvas_acceleration=True,
        images=True,
        media_needs_gesture=False,
    )


# Profiles. A private profile keeps nothing on disk; a stored one keeps its
# cookies, storage and cache in the given folders.
def private_profile(parent=None):
    profile = QWebEngineProfile(parent)
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    return profile


def stored_profile(name: str, parent, *, storage, cache, keep_session_cookies: bool):
    profile = _built_profile(name, parent, storage, cache)
    if profile is None:
        profile = QWebEngineProfile(name, parent)
        profile.setPersistentStoragePath(str(storage))
        profile.setCachePath(str(cache))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    set_keep_session_cookies(profile, keep_session_cookies)
    return profile


def _built_profile(name, parent, storage, cache):
    """A stored profile made with its folders from the start, or None.

    Setting a profile's storage folder after creating it makes Qt WebEngine
    (6.10, 6.11) replace the profile's preferences while Chromium's extension
    code still holds the old ones, so enabling an extension crashes.
    QWebEngineProfileBuilder sets the folders first. PyQt 6.11 exposes the
    builder's methods but not its constructor, so then Qt's own is called.
    """
    try:
        from PyQt6.QtWebEngineCore import QWebEngineProfileBuilder
    except ImportError:
        return None
    try:
        builder = QWebEngineProfileBuilder()
    except TypeError:
        pass  # No constructor in these bindings.
    else:
        return _configure_builder(builder, name, parent, storage, cache)
    import ctypes

    from PyQt6 import sip

    try:
        library = ctypes.CDLL(
            "libQt6WebEngineCore.so.6", mode=os.RTLD_NOLOAD | ctypes.RTLD_GLOBAL
        )
        construct = library._ZN24QWebEngineProfileBuilderC1Ev
        destroy = library._ZN24QWebEngineProfileBuilderD1Ev
    except (OSError, AttributeError):
        return None
    memory = ctypes.create_string_buffer(64)  # The builder holds one pointer.
    construct(memory)
    try:
        builder = sip.wrapinstance(ctypes.addressof(memory), QWebEngineProfileBuilder)
        return _configure_builder(builder, name, parent, storage, cache)
    finally:
        destroy(memory)


def _configure_builder(builder, name, parent, storage, cache):
    builder.setPersistentStoragePath(str(storage))
    builder.setCachePath(str(cache))
    builder.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    return builder.createProfile(name, parent)


def set_keep_session_cookies(profile, keep: bool) -> None:
    """Keep even session cookies across restarts (applies at the next start).

    Restored tabs need them: otherwise sites relying on session cookies sign
    you out at every restart even though their tabs come back.
    """
    policy = QWebEngineProfile.PersistentCookiesPolicy
    profile.setPersistentCookiesPolicy(
        policy.ForcePersistentCookies if keep else policy.AllowPersistentCookies
    )


def versions() -> dict:
    """Qt WebEngine, the Chromium its features come from, and its security fixes."""
    from PyQt6.QtWebEngineCore import (
        qWebEngineChromiumSecurityPatchVersion,
        qWebEngineChromiumVersion,
        qWebEngineVersion,
    )

    return {
        "qt": qWebEngineVersion(),
        "chromium": qWebEngineChromiumVersion(),
        "security": qWebEngineChromiumSecurityPatchVersion(),
    }


def browser_user_agent(engine_default: str) -> str:
    """The engine's user agent without the "QtWebEngine/x" token.

    Sites see that token as an embedded or unsupported browser, and some then
    distrust or drop sign-ins. Removing only the token keeps the Chrome version
    in step with the client-hint headers the engine sends.
    """
    return re.sub(r" QtWebEngine/[\w.]+", "", engine_default)


def user_agent_mismatch(user_agent: str) -> str:
    """A warning when an override claims a different Chrome than the engine."""
    from PyQt6.QtWebEngineCore import qWebEngineChromiumVersion

    claimed = re.search(r"Chrome/(\d+)", user_agent or "")
    engine = qWebEngineChromiumVersion().split(".")[0]
    if claimed and claimed.group(1) != engine:
        return (
            f"This override claims Chrome {claimed.group(1)}, but Steed's engine is "
            f"Chrome {engine} and reports that version to sites in its other "
            "headers. Sites that compare them (such as Discord and Google) may keep "
            "signing you out. Clear the override unless a site needs it."
        )
    return ""


# Tab lifecycle. A frozen tab keeps its page but runs nothing; an unloaded tab
# keeps only its address and history, and loads again when shown.
ACTIVE, FROZEN, UNLOADED = "active", "frozen", "unloaded"
TAB_STATES = (ACTIVE, FROZEN, UNLOADED)
_LIFECYCLE = QWebEnginePage.LifecycleState
_TO_ENGINE = {
    ACTIVE: _LIFECYCLE.Active,
    FROZEN: _LIFECYCLE.Frozen,
    UNLOADED: _LIFECYCLE.Discarded,
}
_FROM_ENGINE = {engine: name for name, engine in _TO_ENGINE.items()}


def tab_state(page) -> str:
    """ACTIVE, FROZEN or UNLOADED; a missing page counts as active."""
    if page is None:
        return ACTIVE
    return _FROM_ENGINE.get(page.lifecycleState(), ACTIVE)


def lightest_state(page) -> str:
    """The furthest the engine allows this page to sleep right now.

    Pages playing media, holding locks or similar stay ACTIVE or FROZEN.
    """
    return _FROM_ENGINE.get(page.recommendedState(), ACTIVE)


def set_tab_state(page, state: str) -> None:
    page.setLifecycleState(_TO_ENGINE[state])


def wake(page) -> None:
    """Resume a frozen page, or reload an unloaded one."""
    if page is not None and tab_state(page) != ACTIVE:
        set_tab_state(page, ACTIVE)


def deeper(state: str, than: str) -> bool:
    """True when state sleeps more than the other (unloaded > frozen > active)."""
    return TAB_STATES.index(state) > TAB_STATES.index(than)


# Scripts. PAGE_WORLD is the page's own JavaScript. STEED_WORLD shares the
# page's document but not its globals, so the page can neither see nor change
# what Steed's scripts define there.
PAGE_WORLD = QWebEngineScript.ScriptWorldId.MainWorld.value
STEED_WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld.value
_INJECTION = {
    "start": QWebEngineScript.InjectionPoint.DocumentCreation,
    "ready": QWebEngineScript.InjectionPoint.DocumentReady,
    "loaded": QWebEngineScript.InjectionPoint.Deferred,
}


def run_script(target, source: str, callback=None, *, world: int):
    """Run JavaScript in a page or one of its frames; callback gets the result.

    The world is required: running Steed's code where the page can reach it, or
    the page's code where it cannot, is a choice to make each time.
    """
    if callback is None:
        target.runJavaScript(source, world)
    else:
        target.runJavaScript(source, world, callback)


def run_in(target, source: str, *arguments):
    """Run JavaScript in a page or frame: run_in(target, source), with a
    world, a callback, or a world and a callback after it, as the engine's
    own call takes them. Without a world it runs in the page's own."""
    world, callback = PAGE_WORLD, None
    if arguments and callable(arguments[0]):
        callback = arguments[0]
    elif arguments:
        world = arguments[0]
        callback = arguments[1] if len(arguments) > 1 else None
    run_script(target, source, callback, world=world)


def page_script(
    name: str,
    source: str,
    *,
    world: int = STEED_WORLD,
    when: str = "ready",
    frames: bool = False,
):
    """A script every page of a profile runs: at "start", "ready" or "loaded".

    Install it with ``add_profile_script`` or a page's own script collection.
    """
    script = QWebEngineScript()
    script.setName(name)
    script.setSourceCode(source)
    script.setWorldId(world)
    script.setInjectionPoint(_INJECTION[when])
    script.setRunsOnSubFrames(frames)
    return script


def add_profile_script(profile, name: str, source: str, **options) -> None:
    profile.scripts().insert(page_script(name, source, **options))


# Page actions by Steed name. find_text keeps search flags here too.
_ACTIONS = {
    "back": QWebEnginePage.WebAction.Back,
    "forward": QWebEnginePage.WebAction.Forward,
    "reload": QWebEnginePage.WebAction.Reload,
    "stop": QWebEnginePage.WebAction.Stop,
    "paste": QWebEnginePage.WebAction.Paste,
    "inspect_element": QWebEnginePage.WebAction.InspectElement,
    "download_link": QWebEnginePage.WebAction.DownloadLinkToDisk,
    "download_image": QWebEnginePage.WebAction.DownloadImageToDisk,
    "download_media": QWebEnginePage.WebAction.DownloadMediaToDisk,
}
# Pages Steed keeps from reaching the web (local_files_reach_web is off). The
# engine also refuses downloads they start, so Steed starts those itself.
LOCAL_SCHEMES = ("file", "steed")


def is_local(url) -> bool:
    return url.scheme() in LOCAL_SCHEMES


PAGE_ACTIONS = tuple(_ACTIONS)


def trigger(page, action: str) -> None:
    page.triggerAction(_ACTIONS[action])


def menu_action(page, action: str):
    """The engine's own menu entry (a QAction) for an action."""
    return page.action(_ACTIONS[action])


def find_text(page, text, *, backward=False, case_sensitive=False, callback=None):
    flags = QWebEnginePage.FindFlag(0)
    if backward:
        flags |= QWebEnginePage.FindFlag.FindBackward
    if case_sensitive:
        flags |= QWebEnginePage.FindFlag.FindCaseSensitively
    if callback is None:
        page.findText(text, flags)
    else:
        page.findText(text, flags, callback)


def background_page(profile, parent):
    """A page nobody sees, in a profile (so its logins apply), for reading a
    site in the background: muted, asking for nothing, opening nothing."""
    page = QWebEnginePage(profile, parent)
    page.setAudioMuted(True)
    page.permissionRequested.connect(lambda permission: permission.deny())
    page.newWindowRequested.connect(lambda request: None)
    page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
    return page


def view_for(page):
    """The widget showing a page, or None (pages can exist without one)."""
    return QWebEngineView.forPage(page) if page is not None else None


def view_around(widget):
    """The web view a widget belongs to (keyboard focus sits in a child of the
    view), or None."""
    while widget is not None:
        if isinstance(widget, QWebEngineView):
            return widget
        widget = widget.parentWidget()
    return None


_FAILURES = {
    "denied": QWebEngineUrlRequestJob.Error.RequestDenied,
    "not_found": QWebEngineUrlRequestJob.Error.UrlNotFound,
    "invalid": QWebEngineUrlRequestJob.Error.UrlInvalid,
    "failed": QWebEngineUrlRequestJob.Error.RequestFailed,
}


def fail_request(job, reason: str) -> None:
    """Answer a steed:// request with "denied", "not_found", "invalid" or "failed"."""
    job.fail(_FAILURES[reason])
