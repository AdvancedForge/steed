"""Engine details stay behind webengine.py; the list of exceptions only shrinks.

Each file below still uses Qt WebEngine directly. Moving one behind
webengine.py means removing it here; adding a new file means using webengine.py
instead, or (when the engine is the point, as in a page class) adding it here
on purpose.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWebEngineCore import QWebEnginePage

import webengine

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", ".git", "tests", "__pycache__", ".claude", "examples"}
DIRECT_ENGINE_FILES = {
    "ad_blocker.py",
    "browser_window.py",
    "cookie_manager.py",
    "cookie_snapshot.py",
    "download_manager.py",
    "page_unload.py",
    "password_service.py",
    "profile_manager.py",
    "services/browser_backend.py",
    "services/engine_capabilities.py",
    "services/navigation_service.py",
    "services/page_api.py",
    "services/page_inspector.py",
    "services/permission_service.py",
    "services/start_pages.py",
    "services/task_network.py",
    "ui/app_panel.py",
    "ui/chatbot_relay.py",
    "ui/devtools.py",
    "ui/html_dialog.py",
    "ui/html_surface.py",
    "ui/web_auth.py",
    "ui/web_chat.py",
}
# Engine names with a Steed equivalent in webengine.py.
WRAPPED = {"LifecycleState", "lifecycleState", "setLifecycleState", "recommendedState"}


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
        self.assertEqual(uses, [], "Use webengine.tab_state() and its helpers")


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


if __name__ == "__main__":
    unittest.main()
