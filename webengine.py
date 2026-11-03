"""Shared Qt WebEngine settings, tab lifecycle and site compatibility helpers.

This module is Steed's boundary with the engine. Code elsewhere asks for
things in Steed's terms (a tab is "active", "frozen" or "unloaded") so that
engine details stay here; tests/test_engine_boundary.py lists the files that
still use Qt WebEngine directly and keeps new ones from appearing.
"""

import os
import re

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
    QWebEngineUrlRequestJob,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

# Names for type hints elsewhere, so annotations need no engine import.
Page = QWebEnginePage
Profile = QWebEngineProfile

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


def prepare_engine_environment() -> None:
    """Call before creating QApplication; keeps any value the user set."""
    for name, value in ENGINE_ENVIRONMENT.items():
        os.environ.setdefault(name, value)


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
}
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


def view_for(page):
    """The widget showing a page, or None (pages can exist without one)."""
    return QWebEngineView.forPage(page) if page is not None else None


_FAILURES = {
    "denied": QWebEngineUrlRequestJob.Error.RequestDenied,
    "not_found": QWebEngineUrlRequestJob.Error.UrlNotFound,
    "invalid": QWebEngineUrlRequestJob.Error.UrlInvalid,
    "failed": QWebEngineUrlRequestJob.Error.RequestFailed,
}


def fail_request(job, reason: str) -> None:
    """Answer a steed:// request with "denied", "not_found", "invalid" or "failed"."""
    job.fail(_FAILURES[reason])
