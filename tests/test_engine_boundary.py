"""Only browser/webengine.py talks to the web engine.

Nothing else imports Qt WebEngine, calls the engine's own methods for what
webengine.py wraps, or overrides the engine's hooks (Steed's pages use
navigation_allowed, new_window and the other Steed hooks instead). Another
engine is then another webengine.py.
"""

import ast
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from qt_test_support import WAIT_SECONDS, application

from browser import webengine

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", ".git", "tests", "__pycache__", ".claude", "examples"}
ENGINE_MODULE = "browser/webengine.py"
# Engine names with a Steed equivalent in webengine.py.
WRAPPED = {
    "LifecycleState",
    "lifecycleState",
    "setLifecycleState",
    "recommendedState",
    "ScriptWorldId",
    "InjectionPoint",
    "WebAction",
    "FindFlag",
    "forPage",
    "WebAttribute",
    "HttpCacheType",
    "PersistentCookiesPolicy",
    "UnknownUrlSchemePolicy",
    # Scripts, frames, cookies, developer tools, requests, permissions,
    # downloads and schemes: webengine.run_script/run_in, frames, main_frame,
    # cookie_store, attach_devtools, devtools_of, Request, RequestFilter,
    # set_request_filter, on_permission, on_download, install_scheme...
    "runJavaScript",
    "mainFrame",
    "cookieStore",
    "devToolsPage",
    "setDevToolsPage",
    "setUrlRequestInterceptor",
    "installUrlSchemeHandler",
    "interceptRequest",
    "requestUrl",
    "firstPartyUrl",
    "resourceType",
    "navigationType",
    "setHttpHeader",
    "featurePermissionRequested",
    "setFeaturePermission",
    "PermissionPolicy",
    "downloadRequested",
    "SavePageFormat",
    "RenderProcessTerminationStatus",
    "NavigationType",
    "WebWindowType",
    "ResourceType",
}
# The engine's hooks; Steed's pages override webengine.Page's instead.
ENGINE_HOOKS = {
    "acceptNavigationRequest",
    "createWindow",
    "javaScriptConsoleMessage",
    "chooseFiles",
    "javaScriptAlert",
    "javaScriptConfirm",
    "javaScriptPrompt",
    "interceptRequest",
    "requestStarted",
}


def sources():
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if not SKIP.intersection(relative.parts):
            yield relative.as_posix(), ast.parse(path.read_text(), str(path))


def imports_engine(tree) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        if any(
            name.startswith(("PyQt6.QtWebEngine", "PyQt6.QtWebChannel"))
            for name in names
        ):
            return True
    return False


class EngineBoundaryTests(unittest.TestCase):
    def test_only_webengine_uses_the_engine(self):
        found = {
            name
            for name, tree in sources()
            if name != ENGINE_MODULE and imports_engine(tree)
        }
        self.assertEqual(found, set(), "Use browser/webengine.py instead")

    def test_wrapped_engine_calls_stay_in_webengine(self):
        uses = []
        for name, tree in sources():
            if name == ENGINE_MODULE:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in WRAPPED:
                    uses.append(f"{name}:{node.lineno} {node.attr}")
        self.assertEqual(uses, [], "Use the matching helper in webengine.py")

    def test_pages_decide_through_steeds_hooks(self):
        overrides = []
        for name, tree in sources():
            if name == ENGINE_MODULE:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in ENGINE_HOOKS:
                    overrides.append(f"{name}:{node.lineno} {node.name}")
        self.assertEqual(
            overrides, [], "Override webengine.Page/View/RequestFilter's hooks instead"
        )

    def test_every_engine_kind_has_a_steed_name(self):
        from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInfo

        for table, enum in (
            (webengine.NAVIGATIONS, QWebEnginePage.NavigationType),
            (webengine.REQUEST_NAVIGATIONS, QWebEngineUrlRequestInfo.NavigationType),
            (webengine.WINDOWS, QWebEnginePage.WebWindowType),
            (webengine.CONSOLE_LEVELS, QWebEnginePage.JavaScriptConsoleMessageLevel),
            (webengine.CRASHES, QWebEnginePage.RenderProcessTerminationStatus),
        ):
            with self.subTest(enum=enum.__name__):
                self.assertEqual(set(table), set(enum))
        # Both navigation enums speak the same words.
        self.assertEqual(
            set(webengine.NAVIGATIONS.values()),
            set(webengine.REQUEST_NAVIGATIONS.values()),
        )

    def test_requests_in_steeds_terms(self):
        from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInfo as Info

        class FakeInfo:
            def __init__(self, resource, navigation):
                self.resource, self.navigation = resource, navigation
                self.blocked = None

            def resourceType(self):
                return self.resource

            def navigationType(self):
                return self.navigation

            def block(self, value):
                self.blocked = value

        resource, nav = Info.ResourceType, Info.NavigationType
        form = webengine.Request(
            FakeInfo(resource.ResourceTypeMainFrame, nav.NavigationTypeFormSubmitted)
        )
        self.assertEqual(
            (form.resource, form.navigation, form.main_frame),
            ("main_frame", "form", True),
        )
        info = FakeInfo(resource.ResourceTypeServiceWorker, nav.NavigationTypeOther)
        worker = webengine.Request(info)
        self.assertEqual(
            (worker.resource, worker.main_frame), ("service_worker", False)
        )
        worker.block()
        self.assertTrue(info.blocked)
        for kind in resource:
            self.assertTrue(
                webengine.Request(FakeInfo(kind, nav.NavigationTypeOther)).resource
            )


class FakePage:
    def __init__(self, state, recommended=None):
        self.state = state
        self.recommended = recommended or state

    def lifecycleState(self):
        return self.state

    def setLifecycleState(self, state):
        self.state = state

    def recommendedState(self):
        return self.recommended


State = QWebEnginePage.LifecycleState


class TabStateTests(unittest.TestCase):
    def test_states_in_steeds_terms(self):
        self.assertEqual(webengine.tab_state(FakePage(State.Active)), "active")
        self.assertEqual(webengine.tab_state(FakePage(State.Frozen)), "frozen")
        self.assertEqual(webengine.tab_state(FakePage(State.Discarded)), "unloaded")
        self.assertEqual(webengine.tab_state(None), "active")
        page = FakePage(State.Active, State.Frozen)
        self.assertEqual(webengine.lightest_state(page), "frozen")
        webengine.set_tab_state(page, webengine.UNLOADED)
        self.assertEqual(page.state, State.Discarded)

    def test_wake_and_order(self):
        page = FakePage(State.Discarded)
        webengine.wake(page)
        self.assertEqual(page.state, State.Active)
        webengine.wake(None)
        active = SimpleNamespace(
            lifecycleState=lambda: State.Active,
            setLifecycleState=lambda state: self.fail("already active"),
        )
        webengine.wake(active)
        self.assertTrue(webengine.deeper("unloaded", "frozen"))
        self.assertTrue(webengine.deeper("frozen", "active"))
        self.assertFalse(webengine.deeper("active", "frozen"))
        self.assertFalse(webengine.deeper("frozen", "frozen"))


class ScriptAndActionTests(unittest.TestCase):
    """The helpers against a real page: worlds stay apart, actions run."""

    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.profile = QWebEngineProfile()
        self.view = QWebEngineView(self.profile)
        self.page = self.view.page()
        # Find in page only counts matches in a shown view.
        self.view.resize(400, 200)
        self.view.show()
        loaded = []
        self.page.loadFinished.connect(loaded.append)
        self.page.setHtml("<p>needle haystack needle</p>", QUrl("https://example.org/"))
        self.wait(lambda: loaded)

    def tearDown(self):
        # Pages go before their profile.
        del self.page
        sip.delete(self.view)
        del self.profile

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def result(self, source, world=webengine.PAGE_WORLD):
        out = []
        webengine.run_script(self.page, source, out.append, world=world)
        self.wait(lambda: out)
        return out[0]

    def test_steed_world_is_hidden_from_the_page(self):
        self.result("window.mark = 'steed'; true", webengine.STEED_WORLD)
        self.assertEqual(self.result("typeof window.mark"), "undefined")
        self.assertEqual(self.result("window.mark", webengine.STEED_WORLD), "steed")
        # Both worlds see the same document.
        self.assertEqual(self.result("document.querySelector('p') !== null"), True)
        webengine.run_script(
            self.page, "document.title = 'no callback'", world=webengine.PAGE_WORLD
        )
        self.wait(lambda: self.page.title() == "no callback")

    def test_profile_scripts_find_and_view(self):
        webengine.add_profile_script(
            self.profile, "test-mark", "document.documentElement.dataset.m = 1"
        )
        self.assertEqual(
            [s.name() for s in self.profile.scripts().find("test-mark")],
            ["test-mark"],
        )
        found = []
        webengine.find_text(self.page, "needle", callback=found.append)
        self.wait(lambda: found)
        self.assertEqual(found[0].numberOfMatches(), 2)
        self.assertIs(webengine.view_for(self.page), self.view)
        self.assertIsNone(webengine.view_for(None))
        for action in webengine.PAGE_ACTIONS:
            self.assertIsNotNone(webengine.menu_action(self.page, action))

    def test_options_profiles_and_versions(self):
        settings = self.page.settings()
        webengine.set_options(settings, javascript=False, webgl=False)
        self.assertFalse(webengine.option(settings, "javascript"))
        webengine.set_options(settings, javascript=True)
        self.assertTrue(webengine.option(settings, "javascript"))
        for name in webengine.PAGE_OPTIONS:
            webengine.option(settings, name)
        private = webengine.private_profile()
        self.assertTrue(private.isOffTheRecord())
        self.assertEqual(
            private.httpCacheType(), QWebEngineProfile.HttpCacheType.MemoryHttpCache
        )
        policy = QWebEngineProfile.PersistentCookiesPolicy
        webengine.set_keep_session_cookies(self.profile, True)
        # Off-the-record profiles keep nothing; the setting is still recorded
        # for stored profiles, which tests/test_navigation_and_privacy checks.
        self.assertIn(
            self.profile.persistentCookiesPolicy(),
            (policy.ForcePersistentCookies, policy.NoPersistentCookies),
        )
        found = webengine.versions()
        self.assertEqual(set(found), {"qt", "chromium", "security"})
        self.assertRegex(found["chromium"], r"^\d+\.")
        del private


if __name__ == "__main__":
    unittest.main()
