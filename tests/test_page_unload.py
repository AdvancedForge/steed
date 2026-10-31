"""Sites save state on unload before Steed closes, unloads or switches away.

The fixture behaves like Discord: it takes its sign-in token out of local
storage while open and writes it back on beforeunload.
"""

import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage
from qt_test_support import application, keep_directory

import memory_modes
from browser_window import BrowserWindow

State = QWebEnginePage.LifecycleState

APP = b"""<title>app</title><script>
window.token = localStorage.getItem('token') || (location.search.includes('login') ? 'TOKEN' : null);
localStorage.removeItem('token'); history.replaceState(null, '', '/app');
addEventListener('beforeunload', () => { if (window.token) localStorage.setItem('token', window.token); });
</script>"""
STUCK = b"""<title>stuck</title><script>
addEventListener('beforeunload', () => { const end = Date.now() + 8000; while (Date.now() < end) {} });
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            APP
            if self.path.startswith("/app")
            else STUCK
            if self.path.startswith("/stuck")
            else b"<title>probe</title>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class PageUnloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home_patch = patch(
            "pathlib.Path.home", return_value=Path(self.directory.name)
        )
        self.home_patch.start()
        self.window = BrowserWindow()
        self.window.resize(900, 600)
        self.window.show()

    def tearDown(self):
        if not sip.isdeleted(self.window):
            self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    def spin(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            time.sleep(0.01)

    def wait(self, predicate, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def current(self):
        return self.window.tabs[self.window.current_tab_index]

    def js(self, tab, code):
        out = []
        tab.page().runJavaScript(code, out.append)
        self.wait(lambda: bool(out))
        return out[0]

    def open_app(self):
        tab = self.window._new_tab(url=self.origin + "/app?login", return_tab=True)
        self.wait(lambda: tab.title() == "app" and tab.url().path() == "/app")
        self.assertIsNone(self.js(tab, "localStorage.getItem('token')"))
        return tab

    def stored(self):
        tab = self.current()
        tab.setUrl(QUrl(self.origin + "/probe"))
        self.wait(lambda: tab.title() == "probe")
        return self.js(tab, "localStorage.getItem('token')")

    def test_closing_a_tab_lets_the_site_save_its_sign_in(self):
        tab = self.open_app()
        self.window._close_tab(self.window.tabs.index(tab))
        self.wait(lambda: sip.isdeleted(tab))
        self.assertEqual(self.stored(), "TOKEN")

    def test_unloading_a_sleeping_tab_lets_the_site_save_its_sign_in(self):
        memory_modes.set_value(self.window.config, "tab_discard_minutes", 15)
        self.window.profile_manager.registry.settings_changed.emit()
        tab = self.open_app()
        self.window._switch_tab(0)
        page = tab.page()
        self.wait(lambda: page.recommendedState() != State.Active)
        page.setLifecycleState(State.Frozen)
        tab.last_active = time.monotonic() - 3600
        self.wait(lambda: page.recommendedState() == State.Discarded)
        self.window._apply_tab_lifecycle()
        self.wait(lambda: page.lifecycleState() == State.Discarded)
        self.assertEqual(self.stored(), "TOKEN")
        # Selecting it again reloads the site, which finds its sign-in.
        self.window._switch_tab(self.window.tabs.index(tab))
        self.wait(lambda: tab.title() == "app")

    def test_switching_profiles_lets_the_site_save_its_sign_in(self):
        self.open_app()
        self.window._switch_to_profile("work")
        self.spin(0.5)
        self.window._switch_to_profile("global")
        # The restored tab reloads the site, which must find its sign-in.
        self.wait(lambda: any(t.title() == "app" for t in self.window.tabs))
        app = next(t for t in self.window.tabs if t.title() == "app")
        self.wait(lambda: self.js(app, "window.token") == "TOKEN")

    def test_closing_the_window_waits_for_handlers_once(self):
        self.open_app()
        self.window.close()
        self.wait(lambda: sip.isdeleted(self.window) or not self.window.isVisible())
        window = BrowserWindow()
        self.addCleanup(lambda: sip.isdeleted(window) or window.close())
        self.window = window
        # The restored tab reloads the site, which must find its sign-in.
        self.wait(lambda: any(t.title() == "app" for t in window.tabs))
        app = next(t for t in window.tabs if t.title() == "app")
        self.wait(lambda: self.js(app, "window.token") == "TOKEN")

    def test_a_stuck_handler_delays_closing_only_briefly(self):
        tab = self.window._new_tab(url=self.origin + "/stuck", return_tab=True)
        self.wait(lambda: tab.title() == "stuck")
        started = time.monotonic()
        self.window._close_tab(self.window.tabs.index(tab))
        self.wait(lambda: sip.isdeleted(tab), timeout=6)
        self.assertLess(time.monotonic() - started, 4)

    def test_the_setting_can_turn_waiting_off(self):
        import page_unload

        self.window.config.set("save_before_closing", False)
        self.window.profile_manager.registry.settings_changed.emit()
        self.addCleanup(setattr, page_unload, "ENABLED", True)
        self.assertFalse(page_unload.ENABLED)
        tab = self.open_app()
        self.window._close_tab(self.window.tabs.index(tab))
        self.spin(0.05)
        # Closed at once, without the site saving its sign-in.
        self.assertTrue(sip.isdeleted(tab))
        self.assertIsNone(self.stored())
        self.window.config.set("save_before_closing", True)
        self.window.profile_manager.registry.settings_changed.emit()
        self.assertTrue(page_unload.ENABLED)

    def test_steed_pages_close_without_waiting(self):
        tab = self.window._new_tab(url="about:blank", return_tab=True)
        self.window._close_tab(self.window.tabs.index(tab))
        self.spin(0.05)
        self.assertTrue(sip.isdeleted(tab))


if __name__ == "__main__":
    unittest.main()
