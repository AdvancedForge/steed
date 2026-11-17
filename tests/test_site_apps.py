"""Site apps: a website in its own window and applications-menu entry; links
to other sites open in a normal window; the app is not part of sessions."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWidgets import QApplication
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from browser.single_instance import launch_urls
from browser.site_apps import SiteApps


class StoreTests(unittest.TestCase):
    def test_install_writes_a_menu_entry_and_remove_takes_it_away(self):
        application()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data")}):
                apps = SiteApps(root / "config")
                icon = QPixmap(32, 32)
                icon.fill(QColor("#3355ff"))
                app = apps.install("https://mail.example.org/inbox", "Mail $HOME", icon)
                entry = apps.entry_path(app["id"]).read_text()
                self.assertIn("Name=Mail $HOME", entry)
                self.assertIn('--app=https://mail.example.org/inbox"', entry)
                self.assertIn(f"Icon={app['icon']}", entry)
                self.assertTrue(Path(app["icon"]).is_file())
                self.assertNotIn("MimeType", entry)
                self.assertEqual([a["id"] for a in apps.list()], [app["id"]])
                with self.assertRaises(ValueError):
                    apps.install("file:///etc/passwd", "x")
                self.assertTrue(apps.remove(app["id"]))
                self.assertEqual(apps.list(), [])
                self.assertFalse(apps.entry_path(app["id"]).exists())

    def test_app_launches_are_passed_on(self):
        self.assertEqual(
            launch_urls(
                ["--app=https://mail.test/", "--app=file:///x", "app:https://b.test/"]
            ),
            ["app:https://mail.test/", "app:https://b.test/"],
        )


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.show()

    def tearDown(self):
        for window in list(QApplication.topLevelWidgets()):
            if isinstance(window, BrowserWindow) and not window._closing:
                window.close()
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

    def test_an_app_window_keeps_to_its_site(self):
        app = self.window.open_site_app("https://mail.test/inbox")
        self.assertEqual(app.app_url, "https://mail.test/inbox")
        self.assertFalse(app.tab_bar.isVisible())
        self.assertFalse(app.address_bar.isVisible())
        self.assertEqual(len(app.tabs), 1)
        tab = app.tabs[0]
        kinds = QWebEnginePage.NavigationType
        # Its own site (and subdomains) stays; another site opens elsewhere.
        self.assertTrue(
            tab.page().acceptNavigationRequest(
                QUrl("https://cdn.mail.test/x"), kinds.NavigationTypeLinkClicked, True
            )
        )
        self.assertFalse(
            tab.page().acceptNavigationRequest(
                QUrl("https://news.test/"), kinds.NavigationTypeLinkClicked, True
            )
        )
        self.wait(
            lambda: any(
                t.url().toString() == "https://news.test/" for t in self.window.tabs
            )
        )
        # New windows and tabs it asks for go to the normal window too.
        before = len(self.window.tabs)
        tab.createWindow(QWebEnginePage.WebWindowType.WebBrowserTab)
        self.assertEqual(len(self.window.tabs), before + 1)
        self.assertEqual(len(app.tabs), 1)
        # Not part of the profile's saved session.
        with patch.object(app.tab_manager, "save_tabs") as saved:
            app._save_current_tabs()
            saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
