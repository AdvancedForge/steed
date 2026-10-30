"""The HTML tab strip drives real window tabs and follows the saved appearance."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application, keep_directory

from browser_window import BrowserWindow
from layout_preferences import LayoutPreferences
from tab_appearance import CHOICES


class TabStripTests(unittest.TestCase):
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
        self.window.resize(1000, 700)
        self.window.show()
        self.strip = self.window.tab_bar
        self.wait(lambda: self.strip.surface.ready)

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    def wait(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for the tab strip")

    def js(self, code):
        results = []
        self.strip.surface.page.runJavaScript(code, results.append)
        self.wait(lambda: bool(results))
        return results[0]

    def rendered(self):
        return json.loads(
            self.js(
                "JSON.stringify([...document.querySelectorAll('.tab')].map(t => "
                "({id: t.dataset.id, title: t.querySelector('.title')?.textContent ?? '', "
                "active: t.classList.contains('active'), "
                "pinned: t.classList.contains('pinned')})))"
            )
        )

    def settle(self, count):
        self.wait(lambda: len(self.rendered()) == count)

    def test_clicks_select_and_close_real_tabs(self):
        self.window._new_tab(url="about:blank")
        self.window._new_tab(url="about:blank")
        self.settle(len(self.window.tabs))
        first = self.rendered()[0]["id"]
        self.js(f"document.querySelector('[data-id=\"{first}\"]').click()")
        self.wait(lambda: self.window.current_tab_index == 0)
        self.wait(lambda: self.rendered()[0]["active"])
        count = len(self.window.tabs)
        self.js(f"document.querySelector('[data-id=\"{first}\"] .close').click()")
        self.wait(lambda: len(self.window.tabs) == count - 1)
        self.settle(count - 1)
        self.js("document.querySelector('.new-tab').click()")
        self.wait(lambda: len(self.window.tabs) == count)

    def test_drag_reorder_moves_the_window_tab(self):
        self.window._new_tab(url="about:blank", title="Second")
        self.window._new_tab(url="about:blank", title="Third")
        self.settle(3)
        moved = self.window.tabs[0]
        tab_id = self.rendered()[0]["id"]
        self.strip._command({"action": "move", "id": tab_id, "to": 2})
        self.assertIs(self.window.tabs[2], moved)
        self.wait(lambda: self.rendered()[2]["id"] == tab_id)

    def test_titles_icons_and_untrusted_text_render_as_text(self):
        # A strip-only tab: no page load can later replace this title.
        self.strip.insert_tab(self.strip.count(), "<img src=x onerror=alert(1)>")
        self.wait(lambda: self.rendered()[-1]["title"].startswith("<img"))
        self.assertEqual(self.js("document.querySelectorAll('.tab img').length"), 0)

    def test_every_appearance_renders(self):
        for key, (choices, _) in CHOICES.items():
            for choice in choices:
                with self.subTest(key=key, choice=choice):
                    preferences = LayoutPreferences()
                    preferences.set_tabs({key: choice})
                    self.window.apply_layout(preferences)
                    if key in ("position", "style", "size", "overflow"):
                        expected = f"{key if key != 'position' else 'pos'}-{choice}"
                        self.wait(
                            lambda expected=expected: (
                                expected
                                in self.js("document.getElementById('strip').className")
                            )
                        )
                    self.assertGreaterEqual(len(self.rendered()), 1)
        preferences = LayoutPreferences()
        preferences.set_tabs({"position": "left", "vertical_width": 300})
        self.window.apply_layout(preferences)
        self.assertEqual(self.strip.width(), 300)

    def test_pinned_tabs_stay_first_render_compact_and_persist(self):
        window = self.window
        window._new_tab(url="about:blank", title="Second")
        window._new_tab(url="about:blank", title="Third")
        third = window.tabs[2]
        window._set_pinned(third, True)
        self.assertIs(window.tabs[0], third)
        self.wait(lambda: self.rendered()[0]["pinned"])
        self.assertEqual(self.rendered()[0]["title"], "")
        # New tabs opened from a pinned tab land after the pinned block.
        window._switch_tab(0)
        window._new_tab(url="about:blank")
        self.assertIs(window.tabs[0], third)
        self.assertFalse(window.tabs[1].pinned)
        # Drags keep each tab within its own block.
        ordinary = self.rendered()[2]["id"]
        self.strip._command({"action": "move", "id": ordinary, "to": 0})
        self.assertIs(window.tabs[0], third)
        window._set_tab_group_existing(0, "Work")
        self.assertIsNone(third.group)
        saved = window.tab_manager.get_saved_tabs()
        self.assertTrue(saved[0].pinned)
        self.assertFalse(any(state.pinned for state in saved[1:]))
        window._set_pinned(third, False)
        self.assertFalse(window.tab_manager.get_saved_tabs()[0].pinned)

    def test_dragging_the_side_grip_resizes_and_saves_the_width(self):
        from PyQt6.QtCore import QEvent, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent

        from layout_preferences import preferences_from_config

        preferences = LayoutPreferences()
        preferences.set_tabs({"position": "right", "vertical_width": 240})
        self.window.profile_manager.registry.commit_layout(preferences)
        grip = self.strip.grip
        self.assertTrue(grip.isVisibleTo(self.strip))

        def mouse(kind, x, buttons):
            point = QPointF(x, 10)
            return QMouseEvent(
                kind,
                point,
                point,
                Qt.MouseButton.LeftButton,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            )

        held = Qt.MouseButton.LeftButton
        grip.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 500, held))
        # On the right edge, dragging left widens the tabs.
        grip.mouseMoveEvent(mouse(QEvent.Type.MouseMove, 440, held))
        self.assertEqual(self.strip.width(), 300)
        grip.mouseMoveEvent(mouse(QEvent.Type.MouseMove, -2000, held))
        self.assertEqual(self.strip.width(), 480)
        grip.mouseReleaseEvent(
            mouse(QEvent.Type.MouseButtonRelease, -2000, Qt.MouseButton.NoButton)
        )
        saved = preferences_from_config(self.window.config)
        self.assertEqual(saved.tabs["vertical_width"], 480)
        self.assertEqual(saved.tabs["position"], "right")
        top = LayoutPreferences()
        self.window.profile_manager.registry.commit_layout(top)
        self.assertFalse(grip.isVisibleTo(self.strip))

    def test_tab_right_click_menu_is_an_html_popup(self):
        from PyQt6.QtCore import QPoint

        window = self.window
        before = len(window.tabs)
        window._show_tab_menu(0, window.mapToGlobal(QPoint(40, 40)))
        popup = window._html_menu
        self.assertIsNotNone(popup)
        self.assertTrue(popup.context_menu)
        self.wait(lambda: popup.surface.ready and popup.actions)
        labels = {action.text(): token for token, action in popup.actions.items()}
        self.assertIn("Duplicate", labels)
        self.assertIn("Pin Tab", labels)
        self.assertTrue(
            self.js_in(popup.surface, "document.querySelector('footer').hidden")
        )
        popup.command(
            {
                "context": popup.context,
                "sequence": 1,
                "action": "activate",
                "id": labels["Duplicate"],
                "x": 10,
                "y": 10,
            }
        )
        self.assertEqual(len(window.tabs), before + 1)
        self.wait(lambda: window._html_menu is None)

    def test_page_right_click_menu_copies_and_pastes_through_html(self):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QApplication

        # A fresh tab, so a still-loading welcome page cannot replace the fixture.
        tab = self.window._new_tab(url="about:blank", return_tab=True)
        loaded = []
        tab.loadFinished.connect(loaded.append)
        tab.setHtml(
            '<input id=f value="menu copy" style="position:absolute;left:20px;'
            'top:20px;width:300px;height:30px"><input id=g style="position:absolute;'
            'left:20px;top:80px;width:300px;height:30px">'
        )

        def page(code):
            results = []
            tab.page().runJavaScript(code, results.append)
            self.wait(lambda: bool(results))
            return results[0]

        self.wait(lambda: bool(loaded))
        self.wait(lambda: page("!!document.getElementById('g')"))
        target = tab.focusProxy() or tab

        def choose(point, label):
            QTest.mouseClick(
                target,
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.NoModifier,
                point,
            )
            self.wait(lambda: self.window._html_menu is not None)
            popup = self.window._html_menu
            self.wait(lambda: popup.surface.ready and popup.actions)
            names = {a.text().replace("&", ""): t for t, a in popup.actions.items()}
            self.assertIn("Inspect", names)
            popup.command(
                {
                    "context": popup.context,
                    "sequence": 1,
                    "action": "activate",
                    "id": names[label],
                    "x": 10,
                    "y": 10,
                }
            )
            self.wait(lambda: self.window._html_menu is None)

        page(
            "document.getElementById('f').focus(); document.getElementById('f').select(); true"
        )
        choose(QPoint(60, 35), "Copy")
        self.wait(lambda: QApplication.clipboard().text() == "menu copy")
        page("document.getElementById('g').focus(); true")
        choose(QPoint(60, 95), "Paste")
        self.wait(lambda: page("document.getElementById('g').value") == "menu copy")

    def js_in(self, surface, code):
        results = []
        surface.page.runJavaScript(code, results.append)
        self.wait(lambda: bool(results))
        return results[0]

    def test_context_menu_duplicate_and_close_others(self):
        self.window._new_tab(url="about:blank", title="Second")
        self.window._new_tab(url="about:blank", title="Third")
        target = self.window.tabs[1]
        self.window._duplicate_tab(target)
        self.assertEqual(len(self.window.tabs), 4)
        self.window._close_tabs_except(target)
        self.assertEqual(self.window.tabs, [target])
        self.window._new_tab(url="about:blank")
        self.window._new_tab(url="about:blank")
        self.window._close_tabs_after(target)
        self.assertEqual(self.window.tabs, [target])


if __name__ == "__main__":
    unittest.main()
