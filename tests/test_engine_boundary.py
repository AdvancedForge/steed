"""Engine details stay behind webengine.py; the list of exceptions only shrinks.

Each file below still uses Qt WebEngine directly. Moving one behind
webengine.py means removing it here; adding a new file means using webengine.py
instead, or (when the engine is the point, as in a page class) adding it here
on purpose.
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

import webengine

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", ".git", "tests", "__pycache__", ".claude", "examples"}
DIRECT_ENGINE_FILES = {
    "ad_blocker.py",
    "browser_window.py",
    "cookie_snapshot.py",
    "engine_features.py",
    "profile_manager.py",
    "services/browser_backend.py",
    "services/engine_capabilities.py",
    "services/permission_service.py",
    "services/start_pages.py",
    "services/task_network.py",
    "ui/app_panel.py",
    "ui/devtools.py",
    "ui/html_dialog.py",
    "ui/html_surface.py",
    "ui/web_auth.py",
}
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
        if any(name.startswith("PyQt6.QtWebEngine") for name in names):
            return True
    return False


class EngineBoundaryTests(unittest.TestCase):
    def test_only_listed_files_use_the_engine_directly(self):
        found = {
            name
            for name, tree in sources()
            if name != "webengine.py" and imports_engine(tree)
        }
        self.assertEqual(
            found - DIRECT_ENGINE_FILES,
            set(),
            "Use webengine.py, or list the file in DIRECT_ENGINE_FILES on purpose",
        )
        self.assertEqual(
            DIRECT_ENGINE_FILES - found,
            set(),
            "These no longer use the engine directly; remove them from the list",
        )

    def test_wrapped_engine_calls_stay_in_webengine(self):
        uses = []
        for name, tree in sources():
            if name == "webengine.py":
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in WRAPPED:
                    uses.append(f"{name}:{node.lineno} {node.attr}")
        self.assertEqual(uses, [], "Use the matching helper in webengine.py")


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


if __name__ == "__main__":
    unittest.main()
