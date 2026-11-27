"""What a first-time user tripped over (a blind review): typing after Ctrl+T,
stuck suggestions, no bookmark star, Dark doing nothing, empty prompts, a
Start that could not start, History without dates or a way to remove one
page, a tab menu too long to reach Close, and Steed pages named about:blank."""

import time
import unittest
from unittest.mock import PropertyMock, patch

import test_writing_help as fixture
from PyQt6.QtCore import QPoint, QUrl
from PyQt6.QtWidgets import QApplication, QComboBox

from ui.management_pages import HistoryDialog, when


class WhenTests(unittest.TestCase):
    def test_history_says_when(self):
        now = time.time()
        self.assertTrue(when(now).startswith("Today "))
        self.assertTrue(when(now - 86400).startswith("Yesterday "))
        self.assertRegex(when(now - 3 * 86400), r"^\w{3} \d{1,2} \w{3} \d\d:\d\d$")
        self.assertRegex(when(now - 400 * 86400), r"^\d{1,2} \w{3} \d{4}$")
        self.assertEqual(when(0), "")


class WindowTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WritingHelpTests.setUpClass.__func__)
    setUp = fixture.WritingHelpTests.setUp
    tearDown = fixture.WritingHelpTests.tearDown
    wait = fixture.WritingHelpTests.wait

    def test_ctrl_t_opens_at_the_end_ready_to_type(self):
        window = self.window
        window._new_tab(url="about:blank")
        window._switch_tab(0)
        window.activateWindow()
        window.new_tab_from_you()
        self.assertEqual(window.current_tab_index, len(window.tabs) - 1)
        self.app.processEvents()
        self.assertIs(QApplication.focusWidget(), window.address_bar.address_input)

    def test_suggestions_close_when_you_go_and_late_ones_stay_away(self):
        bar = self.window.address_bar
        bar.remote_enabled = True
        bar.address_input.setFocus()
        bar.address_input.setText("weather")
        bar._suggest("weather")
        self.assertTrue(bar.completer.popup().isVisible())
        with patch.object(bar, "url_changed") as went:
            bar._on_return_pressed()
        went.emit.assert_called_once_with("weather")
        self.assertFalse(bar.completer.popup().isVisible())
        # Search suggestions arriving after Enter do not open it again.
        bar._remote_ready("weather", ["weather tomorrow"])
        self.assertFalse(bar.completer.popup().isVisible())

    def test_the_star_shows_and_saves_a_bookmark(self):
        bar = self.window.address_bar
        bar.set_bookmarks([("Example", "https://example.com/")])
        bar.set_url("https://example.com/")
        self.assertTrue(bar.star_action.isVisible())
        self.assertTrue(bar.bookmarked)
        self.assertIn("Edit this bookmark", bar.star_action.toolTip())
        bar.set_url("https://other.test/page")
        self.assertFalse(bar.bookmarked)
        self.assertIn("Bookmark this page", bar.star_action.toolTip())
        bar.set_url("steed:settings")
        self.assertFalse(bar.star_action.isVisible())
        with patch.object(self.window, "_edit_bookmark") as edit:
            bar.set_url("https://other.test/page")
            bar.star_action.trigger()
        edit.assert_called_once()

    def test_bookmarks_are_in_the_main_menu(self):
        menu = self.window._layout_menus["cookies"]
        self.assertEqual(menu.title(), "Bookmarks and History")
        texts = [a.text() for a in menu.actions()]
        self.assertIn("Bookmark This Page…", texts)
        self.assertIn("Manage Bookmarks…", texts)

    def test_prompts_show_their_list_and_buttons(self):
        from ui.prompts import InputDialog

        seen = []

        def shown(box):
            layout = box.layout()
            widgets = (
                [layout.itemAt(i).widget() for i in range(layout.count())]
                if layout
                else []
            )
            seen.append(any(isinstance(w, QComboBox) for w in widgets))
            return 0

        with patch.object(InputDialog, "exec", shown):
            InputDialog.getItem(self.window, "Pick", "Which?", ["A", "B"], 0, False)
        self.assertEqual(seen, [True])

    def test_dark_applies_at_once_from_style(self):
        tab = self.window._new_tab(url="steed:style", return_tab=True)
        page = tab.workspace_controller
        styles = self.window.profile_manager.registry.page_style
        profile = self.window.profile_manager.current_profile_name
        page.command(
            {
                "context": page.context,
                "sequence": page.sequence + 1,
                "action": "theme",
                "value": "dark",
            }
        )
        self.assertEqual(styles.get(profile)["theme"], "dark")
        self.assertTrue(styles.dark(profile))
        self.assertEqual(page.notice, "Steed is dark.")
        page.command(
            {
                "context": page.context,
                "sequence": page.sequence + 1,
                "action": "theme",
                "value": "sepia",
            }
        )
        self.assertTrue(page.error)
        self.assertEqual(styles.get(profile)["theme"], "dark")

    def test_start_without_an_assistant_offers_to_add_one(self):
        self.window._toggle_agent_sidebar(True)
        sidebar = self.window.agent_sidebar
        with (
            patch.object(sidebar, "_agent_choices", return_value=[]),
            patch.object(sidebar, "add_assistant") as add,
            patch.object(
                type(sidebar),
                "state",
                new_callable=PropertyMock,
                return_value={"available": True},
            ),
        ):
            sidebar.start_chat()
        add.assert_called_once()

    def test_history_has_dates_a_menu_and_removes_one_page(self):
        history = self.window.history
        history.record("https://one.test/", "One")
        history.record("https://two.test/", "Two")
        opened = []
        dialog = HistoryDialog(history, self.window, opened.append)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(
            [dialog.items.horizontalHeaderItem(i).text() for i in range(3)],
            ["When", "Page", "Address"],
        )
        self.assertTrue(dialog.items.item(0, 0).text().startswith("Today "))
        dialog.items.selectRow(0)
        dialog.items.setCurrentCell(0, 1)
        with patch("PyQt6.QtWidgets.QMenu.popup"):
            dialog.row_menu()
        texts = [a.text() for a in dialog.menu.actions()]
        for expected in ("Open", "Copy Address", "Remove from History"):
            self.assertIn(expected, texts)
        self.assertEqual(dialog.selected(), "https://two.test/")
        dialog.remove_selected()
        self.assertEqual([e.url for e in history.entries()], ["https://one.test/"])

    def test_the_tab_menu_keeps_close_in_reach(self):
        self.window._new_tab(url="about:blank")
        with patch.object(self.window, "_show_html_menu") as shown:
            self.window._show_tab_menu(0, QPoint(10, 10))
        menu = shown.call_args.args[0]
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertIn("Close Tab", texts)
        self.assertIn("More", texts)
        self.assertLessEqual(len(texts), 16)
        more = next(a.menu() for a in menu.actions() if a.text() == "More")
        self.assertIn("Keep This Tab Loaded", [a.text() for a in more.actions()])

    def test_steed_pages_are_named_in_their_tab(self):
        tab = self.window._new_tab(url="steed:settings", return_tab=True)
        index = self.window.tabs.index(tab)
        self.wait(lambda: self.window.tab_bar.records[index].get("page"))
        self.assertNotEqual(
            self.window.tab_bar.records[index].get("site"), "about:blank"
        )
        self.window._on_tab_url_changed(tab, QUrl("about:blank"))
        self.assertEqual(self.window.tab_bar.records[index].get("page"), "settings")

    def test_describe_it_opens_without_an_assistant(self):
        shown = []
        with (
            patch("ui.assistant_choice.ensure", lambda window, then: then(None)),
            patch.object(
                fixture.BrowserWindow,
                "_present_dialog",
                lambda self, dialog, **_: shown.append(dialog),
            ),
        ):
            self.window.find_in_history()
        self.assertEqual(len(shown), 1)
        self.assertIsNone(shown[0].command)


if __name__ == "__main__":
    unittest.main()
