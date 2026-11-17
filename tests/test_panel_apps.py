"""Panel apps: storage, the App Panel, the data API and steed:apps."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import access_levels
from browser.browser_window import BrowserWindow

APP = """<title>Notes</title><a id=out href="https://example.org/next">Next</a>
<script>
window.stored = localStorage.getItem('k'); localStorage.setItem('k', 'VALUE');
window.result = null;
fetch('steed://' + location.host + '/api/bookmarks').then(r => r.json())
  .then(d => window.result = d).catch(e => window.result = 'failed ' + e);
</script>"""


class PanelAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.resize(1100, 700)
        self.window.show()
        self.pages = self.window.profile_manager.registry.start_pages
        self.profile = self.window.profile_manager.current_profile_name

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def js(self, page, code):
        out = []
        page.runJavaScript(code, out.append)
        self.wait(lambda: bool(out))
        return out[0]

    def shown(self, identity):
        self.window._show_panel_app(identity)
        panel = self.window.app_panel
        # The page's address changes before its document does; wait for the app.
        self.wait(
            lambda: (
                panel.page is not None
                and self.js(panel.page, "location.host") == f"app-{identity}"
            )
        )
        self.wait(lambda: self.js(panel.page, "window.result !== null"))
        return panel

    def test_storage_validation_and_limits(self):
        identity = self.pages.save_app(self.profile, "Notes", APP)
        self.assertEqual(self.pages.app(self.profile, identity)["name"], "Notes")
        self.pages.save_app(self.profile, "Renamed", "<p>x</p>", identity)
        self.assertEqual(self.pages.app(self.profile, identity)["name"], "Renamed")
        for name, html in (("", "<p>"), ("x" * 61, "<p>"), ("ok", "a" * 300000)):
            with self.assertRaises(ValueError):
                self.pages.save_app(self.profile, name, html)
        with self.assertRaises(ValueError):
            self.pages.save_app("search", "Private", "<p>")
        for n in range(11):
            self.pages.save_app(self.profile, f"App {n}", "<p>")
        with self.assertRaises(ValueError):
            self.pages.save_app(self.profile, "Too many", "<p>")
        self.pages.remove_app(self.profile, identity)
        self.assertIsNone(self.pages.app(self.profile, identity))

    def test_panel_shows_apps_with_separate_storage_and_isolated_data(self):
        first = self.pages.save_app(self.profile, "Notes", APP)
        second = self.pages.save_app(self.profile, "Notes", APP)
        panel = self.shown(first)
        self.assertEqual(self.js(panel.page, "window.result.error"), "denied")
        self.assertIsNone(self.js(panel.page, "window.stored"))
        panel.page.triggerAction(panel.page.WebAction.Reload)
        self.wait(lambda: self.js(panel.page, "window.stored") == "VALUE")
        # Another app has its own storage.
        panel = self.shown(second)
        self.assertIsNone(self.js(panel.page, "window.stored"))
        self.assertEqual(self.window.config.get("panel_app"), second)

    def test_access_settings_apply_per_app(self):
        from PyQt6.QtWidgets import QMessageBox

        self.pages.add_bookmark(self.profile, "https://example.org/", "Example")
        first = self.pages.save_app(self.profile, "Notes", APP)
        second = self.pages.save_app(self.profile, "Notes", APP)
        access_levels.apply_preset(self.window.config, "pages", "ask")
        asked = []

        def answer(box):
            asked.append(box.text())
            next(b for b in box.buttons() if b.text() == "Allow this session").click()

        with patch.object(QMessageBox, "show", answer):
            panel = self.shown(first)
            self.assertEqual(self.js(panel.page, "window.result[0].title"), "Example")
            self.assertIn("panel app “Notes”", asked[0])
            # Allowing one app does not allow another.
            panel = self.shown(second)
        self.assertEqual(len(asked), 2)

    def test_links_open_in_tabs_and_websites_cannot_load_apps(self):
        identity = self.pages.save_app(self.profile, "Notes", APP)
        panel = self.shown(identity)
        before = len(self.window.tabs)
        self.js(panel.page, "document.getElementById('out').click(); true")
        self.wait(lambda: len(self.window.tabs) == before + 1)
        self.assertEqual(self.window.tabs[-1].url().host(), "example.org")
        self.assertEqual(panel.page.title(), "Notes")
        tab = self.window.tabs[0]
        tab.setUrl(QUrl("data:text/html,<title>site</title>"))
        self.wait(lambda: tab.title() == "site")
        self.js(
            tab.page(),
            f"fetch('steed://app-{identity}/').then(r => window.got = 'loaded')"
            ".catch(() => window.got = 'blocked'); true",
        )
        self.wait(lambda: self.js(tab.page(), "window.got || ''"))
        self.assertEqual(self.js(tab.page(), "window.got"), "blocked")

    def test_incognito_and_empty_states(self):
        self.window._toggle_app_panel(True)
        panel = self.window.app_panel
        self.assertIn("No panel apps yet", panel.notice.text())
        self.window._switch_to_profile("search")
        self.wait(lambda: "incognito" in panel.notice.text())
        self.assertIsNone(panel.page)

    def test_apps_page_saves_opens_and_deletes(self):
        from ui.prompts import MessageBox

        tab = self.window._new_tab(url="steed:apps", return_tab=True)
        page = tab.workspace_controller
        self.wait(lambda: page.surface.ready)

        def send(**command):
            page.command(
                {"context": page.context, "sequence": page.sequence + 1, **command}
            )

        send(action="save", id=None, name="Notes", html=APP, open=True)
        (app,) = self.pages.state(self.profile)["apps"]
        self.wait(lambda: self.window.app_panel and self.window.app_panel.page)
        send(action="save", id=app["id"], name="", html="<p>")
        self.assertTrue(page.error)
        html_file = Path(self.directory.name) / "tracker.html"
        html_file.write_text("<title>Tracker</title>")
        with patch(
            "ui.apps_page.QFileDialog.getOpenFileName",
            return_value=(str(html_file), ""),
        ):
            send(action="import")
        self.assertEqual(
            [a["name"] for a in self.pages.state(self.profile)["apps"]],
            ["Notes", "tracker"],
        )
        with patch.object(
            MessageBox, "question", return_value=MessageBox.StandardButton.Yes
        ):
            send(action="delete", id=app["id"])
        self.assertIsNone(self.pages.app(self.profile, app["id"]))
        self.assertFalse(sip.isdeleted(self.window.app_panel))
        self.assertIn('"name": "tracker"', json.dumps(self.pages.state(self.profile)))


if __name__ == "__main__":
    unittest.main()
