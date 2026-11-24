"""Mouse gestures and Tabs on the Side."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import browser_window as bw
from ui.mouse_gestures import directions


class DirectionTests(unittest.TestCase):
    def test_strokes_become_letters(self):
        self.assertEqual(directions([(0, 0), (-10, 0), (-40, 2)]), "L")
        self.assertEqual(directions([(0, 0), (0, 40), (0, 80), (50, 80)]), "DR")
        self.assertEqual(directions([(0, 0), (0, -40), (0, 10)]), "UD")
        self.assertEqual(directions([(0, 0), (5, 5), (10, -3)]), "")  # A wobble.


class GestureWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = bw.BrowserWindow()
        self.window.resize(1000, 700)
        self.window.show()
        self.window._new_tab(url="data:text/html,<body>Gestures</body>")
        self.tab = self.window.tabs[self.window.current_tab_index]
        self.wait(lambda: self.tab.focusProxy() is not None)

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

    def draw(self, *points):
        """Right button down at the first point, moved through the rest, up."""
        target = self.tab.focusProxy()

        def send(kind, point, button, buttons):
            local = QPointF(QPoint(*point))
            event = QMouseEvent(
                kind,
                local,
                QPointF(target.mapToGlobal(local)),
                button,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            )
            return QCoreApplication.sendEvent(target, event)

        right = Qt.MouseButton.RightButton
        send(QEvent.Type.MouseButtonPress, points[0], right, right)
        for point in points[1:]:
            send(QEvent.Type.MouseMove, point, Qt.MouseButton.NoButton, right)
        send(QEvent.Type.MouseButtonRelease, points[-1], right, Qt.MouseButton.NoButton)
        self.app.processEvents()

    def test_gestures_are_off_until_turned_on(self):
        count = len(self.window.tabs)
        self.draw((300, 200), (300, 300))
        self.assertEqual(len(self.window.tabs), count)

    def test_drawing_down_opens_a_tab_and_down_right_closes_it(self):
        self.window.config.set("mouse_gestures", True)
        count = len(self.window.tabs)
        self.draw((300, 200), (300, 260), (300, 320))
        self.assertEqual(len(self.window.tabs), count + 1)
        self.assertIn("New tab", self.window.status_bar.currentMessage())
        self.tab = self.window.tabs[self.window.current_tab_index]
        self.wait(lambda: self.tab.focusProxy() is not None)
        self.draw((300, 200), (300, 260), (380, 260))
        self.assertEqual(len(self.window.tabs), count)

    def test_a_plain_right_click_still_opens_the_page_menu(self):
        self.window.config.set("mouse_gestures", True)
        menus = []
        with patch.object(
            bw.BrowserTab, "contextMenuEvent", lambda tab, event: menus.append(tab)
        ):
            self.draw((300, 200), (304, 203))
            self.wait(lambda: menus)
        self.assertIs(menus[0], self.tab)

    def test_tabs_on_the_side_and_back(self):
        self.window.set_side_tabs(True)
        self.assertEqual(self.window.layout_preferences.tabs["position"], "left")
        self.window.set_side_tabs(False)
        self.assertEqual(self.window.layout_preferences.tabs["position"], "top")


if __name__ == "__main__":
    unittest.main()
