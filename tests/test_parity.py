"""Everyday browser basics: printing, new windows and quitting, full screen,
page source, searching a selection, dark websites, opening local files from
other apps, and becoming the default browser."""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWidgets import QApplication, QMenu
from qt_test_support import WAIT_SECONDS, application, keep_directory

import default_browser
from browser_window import BrowserWindow
from single_instance import launch_urls


class LaunchTests(unittest.TestCase):
    def test_local_files_and_web_addresses_are_passed_on(self):
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "notes.html"
            page.write_text("<p>hi")
            urls = launch_urls(
                [
                    "https://example.org/",
                    str(page),
                    QUrl.fromLocalFile(str(page)).toString(),
                    str(Path(directory) / "missing.html"),
                    "--flag",
                    "javascript:alert(1)",
                    "ftp://example.org/",
                ]
            )
        local = QUrl.fromLocalFile(str(page.resolve())).toString()
        self.assertEqual(urls, ["https://example.org/", local, local])


class DefaultBrowserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_DATA_HOME": self.directory.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def test_the_entry_handles_web_links_and_html_files(self):
        path = default_browser.ensure_entry()
        text = path.read_text()
        self.assertIn("MimeType=x-scheme-handler/http;x-scheme-handler/https;", text)
        self.assertIn("text/html;", text)
        self.assertIn("steed.svg", text)  # Steed's own icon.

    def test_making_it_default_uses_xdg_settings_then_xdg_mime(self):
        calls = []

        def run(command):
            calls.append(command)
            code = 1 if command[1] == "set" else 0
            return subprocess.CompletedProcess(command, code, "", "")

        tools = {
            "xdg-settings": "/usr/bin/xdg-settings",
            "xdg-mime": "/usr/bin/xdg-mime",
        }
        with (
            patch.object(default_browser.shutil, "which", side_effect=tools.get),
            patch.object(default_browser, "_run", side_effect=run),
        ):
            message = default_browser.make_default()
        self.assertIn("web links", message)
        self.assertEqual(
            calls[0],
            ["/usr/bin/xdg-settings", "set", "default-web-browser", "steed.desktop"],
        )
        self.assertEqual(
            calls[1][:3], ["/usr/bin/xdg-mime", "default", "steed.desktop"]
        )
        self.assertTrue(default_browser.entry_path().is_file())
        with (
            patch.object(default_browser.shutil, "which", return_value=None),
            self.assertRaises(OSError),
        ):
            default_browser.make_default()
        answer = subprocess.CompletedProcess([], 0, "steed.desktop\n", "")
        with (
            patch.object(default_browser.shutil, "which", side_effect=tools.get),
            patch.object(default_browser, "_run", return_value=answer),
        ):
            self.assertTrue(default_browser.is_default())


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
        self.tab = self.window.tabs[self.window.current_tab_index]

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

    def windows(self):
        return [
            w
            for w in QApplication.topLevelWidgets()
            if isinstance(w, BrowserWindow) and not w._closing and w.isVisible()
        ]

    def test_new_windows_links_in_new_windows_and_quit(self):
        self.window.new_window()
        self.assertEqual(len(self.windows()), 2)
        opened = self.tab.createWindow(QWebEnginePage.WebWindowType.WebBrowserWindow)
        self.assertEqual(len(self.windows()), 3)
        self.assertIsNot(opened.browser_window, self.window)
        # A background tab leaves the current tab in front.
        before = self.window.current_tab_index
        self.tab.createWindow(QWebEnginePage.WebWindowType.WebBrowserBackgroundTab)
        self.assertEqual(self.window.current_tab_index, before)
        self.assertEqual(len(self.window.tabs), 2)
        self.window.quit_steed()
        self.wait(lambda: not self.windows())

    def test_closing_a_window_leaves_the_windows_it_opened(self):
        second = self.window.new_window()
        incognito = second._open_window("search")
        self.window.close()
        self.wait(lambda: self.window._closing)
        self.app.processEvents()
        self.assertTrue(second.isVisible() and not second._closing)
        self.assertTrue(incognito.isVisible() and not incognito._closing)
        self.assertIsNone(second._opener)  # On its own now.
        second.close()
        self.assertTrue(incognito.isVisible() and not incognito._closing)

    def test_quitting_keeps_every_window_for_next_time(self):
        second = self.window.new_window()
        with patch.object(type(self.window.tab_manager), "release") as released:
            self.window.quit_steed()
            self.wait(lambda: not self.windows())
        self.assertTrue(all(c.kwargs.get("keep") for c in released.call_args_list))
        self.assertGreaterEqual(len(released.call_args_list), 2)
        self.assertTrue(second._closing)

    def test_print_full_screen_and_page_source(self):
        from PyQt6.QtPrintSupport import QPrintDialog

        self.tab.setHtml("<title>Doc</title><p>text", QUrl("https://doc.test/"))
        self.wait(lambda: self.tab.title() == "Doc")
        printed = []

        def fake_print(printer):
            printed.append(printer)
            self.tab.printFinished.emit(True)

        with (
            patch.object(
                QPrintDialog, "exec", return_value=QPrintDialog.DialogCode.Accepted
            ),
            patch.object(self.tab, "print", side_effect=fake_print),
        ):
            self.window.print_page(self.tab)
        self.assertEqual(len(printed), 1)
        self.assertIsNone(self.tab._printer)  # Kept only until the job ends.
        with patch.object(
            QPrintDialog, "exec", return_value=QPrintDialog.DialogCode.Rejected
        ):
            with patch.object(self.tab, "print") as never:
                self.window.print_page(self.tab)
            never.assert_not_called()
        self.window.toggle_full_screen()
        self.wait(self.window.isFullScreen)
        self.assertTrue(self.window.full_screen_action.isChecked())
        self.window.toggle_full_screen()
        self.wait(lambda: not self.window.isFullScreen())
        self.window.view_source()
        source = self.window.tabs[self.window.current_tab_index]
        self.wait(lambda: source.url().toString() == "view-source:https://doc.test/")

    def test_search_a_selection_and_dark_websites(self):
        class Request:
            def selectedText(self):
                return "  tide   pools  "

        menu = QMenu()
        menu.addAction("Copy")
        self.window._add_search_item(menu, self.tab, Request())
        action = menu.actions()[0]
        self.assertEqual(action.text(), "Search DuckDuckGo for “tide pools”")
        action.trigger()
        searched = self.window.tabs[self.window.current_tab_index].url()
        self.assertEqual(searched.host(), "www.duckduckgo.com")
        self.assertEqual(searched.query(), "q=tide pools")
        dark = QWebEngineSettings.WebAttribute.ForceDarkMode
        self.assertFalse(self.tab.settings().testAttribute(dark))
        self.window.config.set("dark_pages", True)
        self.window._settings_changed()
        self.assertTrue(all(t.settings().testAttribute(dark) for t in self.window.tabs))
        self.assertTrue(
            self.window._new_tab(return_tab=True).settings().testAttribute(dark)
        )

    def test_the_bookmarks_bar(self):
        from ui.bookmarks_bar import items_of

        self.assertEqual(
            items_of(
                [
                    {"title": "A", "url": "https://a.test/"},
                    {"title": "Docs", "url": "https://d.test/", "folder": "Work"},
                    {"title": "B", "url": "https://b.test/"},
                    {"title": "Qt", "url": "https://q.test/", "folder": "Work"},
                    {"title": "", "url": ""},
                ]
            ),
            [
                ("link", "A", "https://a.test/"),
                (
                    "folder",
                    "Work",
                    [("Docs", "https://d.test/"), ("Qt", "https://q.test/")],
                ),
                ("link", "B", "https://b.test/"),
            ],
        )
        bar = self.window.bookmarks_bar
        self.assertFalse(bar.isVisible())  # Off until you want it.
        pages = self.window.profile_manager.registry.start_pages
        name = self.window.profile_manager.current_profile_name
        pages.add_bookmark(name, "https://a.test/", "A")
        self.window.bookmarks_bar_action.trigger()
        self.assertTrue(bar.isVisible())
        self.assertTrue(self.window.config.get("bookmarks_bar"))
        self.wait(lambda: [b.text() for b in bar.buttons] == ["A"])
        pages.add_bookmark(name, "https://b.test/", "B", folder="Work")
        self.wait(lambda: [b.text() for b in bar.buttons] == ["A", "Work ▾"])
        with patch.object(self.window, "_navigate_tab_to_url") as went:
            bar.buttons[0].click()
        went.assert_called_once_with(self.tab, "https://a.test/")
        before = len(self.window.tabs)
        bar.opened.emit("https://a.test/", True)
        self.assertEqual(len(self.window.tabs), before + 1)
        self.window.bookmarks_bar_action.trigger()
        self.assertFalse(bar.isVisible())

    def test_spell_check_follows_the_setting(self):
        from services import spellcheck

        registry = self.window.profile_manager.registry
        folder = Path(self.directory.name) / "dicts"
        folder.mkdir()
        with patch.object(spellcheck, "folder", return_value=folder):
            spell = registry.spellcheck
            spell.where = folder
            with patch.object(spell, "fetch") as fetch:
                self.window.config.set("spellcheck_language", "en-GB")
                registry.apply_spellcheck()
                fetch.assert_called_once_with("en-GB")  # Not here yet: download.
            (folder / "en-GB.bdic").write_bytes(b"BDic")
            registry.apply_spellcheck()
            profile = registry.profiles[
                self.window.profile_manager.current_profile_name
            ]
            self.assertTrue(profile.isSpellCheckEnabled())
            self.assertEqual(profile.spellCheckLanguages(), ["en-GB"])
            self.window.config.set("spellcheck_language", "")
            registry.apply_spellcheck()
            self.assertFalse(profile.isSpellCheckEnabled())
        self.assertEqual(
            spellcheck.newest(
                ["en-US-10-1.bdic", "en-US-10-2.bdic", "en-USX-1-1.bdic"], "en-US"
            ),
            "en-US-10-2.bdic",
        )

    def test_agent_setup_is_one_submenu(self):
        labels = [a.text() for a in self.window._layout_menus["agents"].actions()]
        self.assertEqual(labels[0], "Agent Sidebar")
        self.assertIn("Access and Setup", labels)
        sub = next(
            a.menu()
            for a in self.window._layout_menus["agents"].actions()
            if a.text() == "Access and Setup"
        )
        self.assertIn(self.window.auto_approval_action, sub.actions())
        self.assertNotIn(
            self.window.auto_approval_action,
            self.window._layout_menus["agents"].actions(),
        )


if __name__ == "__main__":
    unittest.main()
