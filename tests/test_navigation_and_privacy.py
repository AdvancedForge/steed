"""Back/forward follow history; incognito starts fresh; sign-ins survive restarts."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWebEngineCore import QWebEngineProfile, qWebEngineChromiumVersion
from PyQt6.QtWidgets import QApplication
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from browser.webengine import browser_user_agent, user_agent_mismatch


def page(title):
    return QUrl(f"data:text/html,<title>{title}</title><h1>{title}</h1>")


class NavigationAndPrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

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
        self.tab = self.window.tabs[0]

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    def wait(self, predicate, timeout=WAIT_SECONDS):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def visit(self, title):
        self.tab.setUrl(page(title))
        self.wait(lambda: self.tab.title() == title)

    def buttons(self):
        bar = self.window.address_bar
        return bar.back_btn.isEnabled(), bar.forward_btn.isEnabled()

    def test_buttons_follow_history_after_every_navigation(self):
        self.visit("one")
        self.visit("two")
        self.wait(lambda: self.buttons() == (True, False))
        self.window.address_bar.back_btn.click()
        self.wait(lambda: self.tab.title() == "one")
        self.wait(lambda: self.buttons()[1])
        self.window.address_bar.forward_btn.click()
        self.wait(lambda: self.tab.title() == "two")
        self.wait(lambda: self.buttons() == (True, False))
        # Shortcuts stay enabled so an early key press is never swallowed.
        self.assertTrue(self.window.forward_action.isEnabled())

    def test_keyboard_and_mouse_side_buttons_navigate_from_the_page(self):
        self.visit("one")
        self.visit("two")

        # Navigation replaces the page's render widget; find the current one.
        def target():
            return self.tab.focusProxy() or self.tab

        QTest.keyClick(target(), Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
        self.wait(lambda: self.tab.title() == "one")
        QTest.keyClick(target(), Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
        self.wait(lambda: self.tab.title() == "two")
        widget = target()
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            QApplication.sendEvent(
                widget,
                QMouseEvent(
                    kind,
                    QPointF(40, 40),
                    QPointF(widget.mapToGlobal(QPoint(40, 40))),
                    Qt.MouseButton.BackButton,
                    Qt.MouseButton.BackButton
                    if kind == QEvent.Type.MouseButtonPress
                    else Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                ),
            )
        self.wait(lambda: self.tab.title() == "one")
        # One click goes back exactly once: "two" is still ahead.
        self.assertTrue(self.tab.history().canGoForward())
        self.assertFalse(self.tab.history().canGoBack())

    def test_session_cookies_are_kept_when_tabs_are_restored(self):
        registry = self.window.profile_manager.registry
        policy = QWebEngineProfile.PersistentCookiesPolicy
        profile = self.window.profile_manager.current_profile
        self.assertEqual(
            profile.persistentCookiesPolicy(), policy.ForcePersistentCookies
        )
        self.window.config.set("restore_tabs_on_startup", False)
        registry.refresh_settings()
        self.assertEqual(
            profile.persistentCookiesPolicy(), policy.AllowPersistentCookies
        )

    def test_default_user_agent_matches_the_engine(self):
        agent = self.window.profile_manager.current_profile.httpUserAgent()
        engine = qWebEngineChromiumVersion().split(".")[0]
        self.assertNotIn("QtWebEngine", agent)
        self.assertIn(f"Chrome/{engine}.", agent)
        self.assertEqual(
            browser_user_agent("A QtWebEngine/6.11.2 Chrome/140.0.0.0 B"),
            "A Chrome/140.0.0.0 B",
        )
        self.assertEqual(user_agent_mismatch(agent), "")
        self.assertEqual(user_agent_mismatch(""), "")
        warning = user_agent_mismatch("Mozilla/5.0 Chrome/99.0.0.0 Safari/537.36")
        self.assertIn("Chrome 99", warning)
        self.assertIn(f"Chrome {engine}", warning)

    def test_incognito_starts_fresh_after_leaving_it(self):
        registry = self.window.profile_manager.registry
        self.window._switch_to_profile("search")
        first = registry.search_profile
        self.assertIs(self.window.profile_manager.current_profile, first)
        self.window._switch_to_profile("global")
        self.wait(lambda: registry.search_profile is not first)
        self.assertTrue(registry.search_profile.isOffTheRecord())
        self.assertIs(
            self.window.profile_manager.search_profile, registry.search_profile
        )
        self.assertIn("search", registry.snapshots)
        self.window._switch_to_profile("search")
        self.assertIs(
            self.window.profile_manager.current_profile, registry.search_profile
        )

    def test_incognito_is_kept_while_another_window_uses_it(self):
        registry = self.window.profile_manager.registry
        other = BrowserWindow()
        self.addCleanup(other.close)
        other._switch_to_profile("search")
        kept = registry.search_profile
        self.window._switch_to_profile("search")
        self.window._switch_to_profile("global")
        end = time.monotonic() + 2
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.02)
        self.assertIs(registry.search_profile, kept)
        self.assertFalse(registry.reset_search_profile())


if __name__ == "__main__":
    unittest.main()
