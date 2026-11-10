"""Real engine routing, profile-local preferences and start-page isolation."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow
from config import Config
from services.news_feed import feed_items
from services.start_pages import NEW_TAB, WELCOME, clean_settings, image_data
from utils import save_json


class StartPageTests(unittest.TestCase):
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
        self.pages = self.window.profile_manager.registry.start_pages
        # These tests cover the classic page's sections (the simple page has
        # its own tests below).
        self.pages.configure(
            "global", {**self.pages.state("global")["settings"], "layout": "classic"}
        )

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    def wait(self, predicate):
        deadline = time.monotonic() + WAIT_SECONDS
        result = predicate()
        while not result and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
            result = predicate()
        self.assertTrue(result)

    def js(self, code, tab=None):
        results = []
        (tab or self.window.tabs[self.window.current_tab_index]).page().runJavaScript(
            code, results.append
        )
        self.wait(lambda: bool(results))
        return results[0]

    def loaded(self, tab):
        try:
            self.wait(
                lambda: (
                    self.js("location.href", tab)
                    == tab.page().requestedUrl().toString()
                    and self.js("document.readyState", tab) == "complete"
                    and bool(self.js("document.title", tab))
                )
            )
        except AssertionError:
            self.fail(
                f"Page did not finish: {tab.page().requestedUrl().toString()} / {self.js('location.href', tab)} / {self.js('document.title', tab)}"
            )

    def test_profile_named_like_global_storage_stays_listed(self):
        manager = self.window.profile_manager
        self.assertTrue(manager.create_profile("global_profile"))
        self.assertEqual(manager.list_profiles(), ["global", "global_profile"])
        self.assertNotEqual(
            manager.get_profile_path("global_profile"),
            manager.get_profile_path("global"),
        )

    def test_fresh_global_welcome_newtab_and_separate_home(self):
        self.assertEqual(self.window.profile_manager.current_profile_name, "global")
        self.assertEqual(self.window.profile_manager.list_profiles(), ["global"])
        self.assertFalse((self.window.config.profiles_dir / "default").exists())
        tab = self.window.tabs[0]
        self.loaded(tab)
        self.assertEqual(self.js("document.title", tab), "Welcome to Steed")
        self.assertEqual(self.window.address_bar.address_input.text(), WELCOME)
        self.js(
            "document.querySelector('a[href=\"steed://action/start\"]').click()", tab
        )
        self.wait(lambda: self.window.config.get("welcome_completed", False))
        self.loaded(tab)
        self.assertEqual(self.js("document.title", tab), "New Tab")
        self.window.config.update({"home_page": "about:blank"})
        second = self.window._new_tab(return_tab=True)
        self.loaded(second)
        self.assertEqual(self.js("document.title", second), "New Tab")
        self.window._go_home()
        self.wait(lambda: second.url().toString() == "about:blank")
        self.window.config.update({"new_tab_page": "about:blank"})
        third = self.window._new_tab(return_tab=True)
        self.wait(lambda: third.url().toString() == "about:blank")

    def test_aliases_reload_and_restorable_urls(self):
        tab = self.window.tabs[0]
        completed = []
        tab.loadFinished.connect(completed.append)
        for address in (NEW_TAB, "steed::newtab", "steed://newtab/"):
            count = len(completed)
            self.window._navigate_to_url(address)
            self.wait(lambda count=count: len(completed) > count and completed[-1])
            self.loaded(tab)
            self.assertEqual(self.js("document.title", tab), "New Tab")
            self.assertEqual(self.window.address_bar.address_input.text(), NEW_TAB)
            count = len(completed)
            self.window._reload()
            self.wait(lambda count=count: len(completed) > count and completed[-1])
            self.loaded(tab)
            self.assertEqual(self.js("document.title", tab), "New Tab")
        self.window._save_current_tabs()
        self.assertEqual(
            self.window.tab_manager.get_saved_tabs()[0].url, "steed://newtab/"
        )
        self.assertEqual(self.window.history.entries(), [])

    def test_legacy_selection_preserves_data_and_is_not_reserved(self):
        config = self.window.config
        directory = config.profiles_dir / "default"
        directory.mkdir()
        (directory / "keep.txt").write_text("existing data")
        save_json(
            config.config_file,
            {"default_profile": "default", "home_page": "https://work.example.org"},
        )
        restored = Config()
        self.assertEqual(restored.default_profile, "global")
        self.assertEqual(restored.get("home_page"), "https://work.example.org")
        self.assertIn("default", self.window.profile_manager.list_profiles())
        self.assertTrue(
            self.window.profile_manager.rename_profile("default", "saved-browsing")
        )
        self.assertEqual(
            (config.profiles_dir / "saved-browsing" / "keep.txt").read_text(),
            "existing data",
        )

    def test_profile_bookmarks_escape_and_private_does_not_persist(self):
        self.pages.add_bookmark(
            "global", "https://example.org", "<img src=x onerror=alert(1)>"
        )
        data = self.pages.render("global", "newtab").decode()
        self.assertIn("&lt;img", data)
        self.assertNotIn("<img src=x", data)
        self.assertEqual(self.pages.state("other")["bookmarks"], [])
        self.pages.add_bookmark("search", "https://private.example.org", "Private")
        self.assertIsNone(self.pages.path("search"))
        self.assertNotIn("Private", self.pages.render("global", "newtab").decode())
        self.pages._states.clear()
        self.assertEqual(
            self.pages.state("global")["bookmarks"][0]["title"],
            "<img src=x onerror=alert(1)>",
        )
        self.assertEqual(self.pages.state("search")["bookmarks"], [])
        with self.assertRaises(ValueError):
            self.pages.add_bookmark("global", "javascript:alert(1)", "Unsafe")

    def test_custom_html_inline_js_no_native_actions_and_reset(self):
        state = self.pages.state("global")
        self.pages.configure(
            "global",
            state["settings"],
            '<title>Custom</title><h1 id="answer">Hi</h1><script>document.querySelector("h1").textContent="Edited";</script><a id="customize" href="steed://action/customize">Edit</a>',
        )
        tab = self.window._new_tab(return_tab=True)
        self.loaded(tab)
        self.assertEqual(
            self.js("document.querySelector('#answer').textContent", tab), "Edited"
        )
        with patch.object(self.window, "_customize_new_tab") as customize:
            self.js("document.querySelector('#customize').click()", tab)
            for _ in range(10):
                self.app.processEvents()
            customize.assert_not_called()
        self.pages.configure("global", state["settings"], "")
        self.loaded(tab)
        self.wait(lambda: self.js("document.title", tab) == "New Tab")

    def test_sections_image_and_closed_tabs(self):
        from PyQt6.QtGui import QImage

        image = QImage(50, 30, QImage.Format.Format_RGB32)
        image.fill(0xFFFFFF)
        path = Path(self.directory.name) / "image.png"
        image.save(str(path))
        settings = clean_settings(
            {
                "modules": ["image", "history", "search"],
                "image": image_data(path),
                "layout": "classic",
            }
        )
        self.pages.configure("global", settings)
        data = self.pages.render("global", "newtab").decode()
        self.assertIn("data:image/png;base64,", data)
        self.assertIn("has-background", data)
        self.assertIn("--newtab-image: url(", data)
        self.assertNotIn('<figure class="picture">', data)
        self.assertIn("Recent visits", data)
        self.window._new_tab(url="about:blank")
        self.window._close_current_tab()
        self.assertTrue(self.window.closed_tabs.entries("global"))
        self.assertFalse(self.window.closed_tabs.entries("search"))

    def test_feed_parser_handles_rss_atom_and_rejects_entities(self):
        rss = b"<rss><channel><item><title>One</title><link>https://news.example.org/one</link></item><item><title>Unsafe</title><link>javascript:alert(1)</link></item></channel></rss>"
        self.assertEqual(feed_items(rss), [("One", "https://news.example.org/one")])
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Two</title><link href="https://news.example.org/two"/></entry></feed>'
        self.assertEqual(feed_items(atom), [("Two", "https://news.example.org/two")])
        with self.assertRaises(ValueError):
            feed_items(
                b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss/>'
            )

    def test_search_form_preserves_spaces_plus_and_punctuation(self):
        tab = self.window._new_tab(return_tab=True)
        self.loaded(tab)
        with patch.object(self.window, "_navigate_tab_to_url") as navigate:
            self.js(
                "document.querySelector('#query').value = 'red fox + & %'; document.querySelector('#query').form.requestSubmit();",
                tab,
            )
            self.wait(lambda: navigate.called)
            self.assertEqual(navigate.call_args.args, (tab, "red fox + & %"))

    def test_manual_dialog_saves_both_pages_atomically_and_cancel_does_not_apply(self):
        from ui.start_page_dialog import StartPageDialog

        dialog = StartPageDialog(self.pages, "global")
        try:
            dialog.use_custom.setChecked(True)
            dialog.html.setPlainText("<h1>Custom new tab</h1>")
            dialog.page_choice.setCurrentIndex(1)
            dialog.use_custom.setChecked(True)
            dialog.html.setPlainText("<h1>Custom welcome</h1>")
            self.assertEqual(self.pages.state("global")["html"], "")
            dialog.save()
            state = self.pages.state("global")
            self.assertEqual(state["html"], "<h1>Custom new tab</h1>")
            self.assertEqual(state["welcome_html"], "<h1>Custom welcome</h1>")
            previous = self.pages.path("global").read_bytes()
            with patch(
                "services.start_pages.save_json",
                side_effect=OSError("synthetic write failure"),
            ):
                with self.assertRaises(OSError):
                    self.pages.configure("global", state["settings"], "replacement")
            self.assertEqual(
                self.pages.state("global")["html"], "<h1>Custom new tab</h1>"
            )
            self.assertEqual(self.pages.path("global").read_bytes(), previous)
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_minimal_search_and_queued_feed_cancellation(self):
        self.pages.configure(
            "global", {"modules": ["search"], "minimal": True, "layout": "classic"}
        )
        tab = self.window._new_tab(return_tab=True)
        self.loaded(tab)
        self.assertEqual(
            self.js("getComputedStyle(document.querySelector('header')).display", tab),
            "none",
        )
        self.assertEqual(
            self.js("getComputedStyle(document.querySelector('.hero')).display", tab),
            "none",
        )
        with patch.object(self.pages.news, "fetch") as fetch:
            self.pages.news.snapshot("global", "https://feed.example.org/rss")
            self.pages.news.forget("global")
            self.app.processEvents()
            fetch.assert_not_called()

    def test_optional_feed_fetch_is_bounded_and_cookie_free(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from PyQt6.QtNetwork import QNetworkCookie

        cookies = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cookies.append(self.headers.get("Cookie", ""))
                payload = b"<rss><channel><item><title>Local article</title><link>https://news.example.org/one</link></item></channel></rss>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}/feed"
        self.pages.news.network.cookieJar().setCookiesFromUrl(
            [QNetworkCookie(b"private", b"value")], QUrl(url)
        )
        try:
            self.pages.news.snapshot("global", url)
            self.wait(lambda: "global" in self.pages.news.cache)
            self.assertEqual(cookies, [""])
            self.assertEqual(
                self.pages.news.snapshot("global", url)[0],
                [("Local article", "https://news.example.org/one")],
            )
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_news_updates_keep_search_draft_and_page_position(self):
        feed = "https://feed.example.org/rss"
        self.pages.configure(
            "global", {"modules": ["search", "news"], "feed": feed, "layout": "classic"}
        )
        self.pages.news.cache["global"] = (
            feed,
            time.monotonic(),
            [("First", "https://news.example.org/one")],
            "",
        )
        tab = self.window._new_tab(return_tab=True)
        self.loaded(tab)
        self.js("document.querySelector('#query').value='my unfinished search'", tab)
        self.pages.news.cache["global"] = (
            feed,
            time.monotonic(),
            [("Second", "https://news.example.org/two")],
            "",
        )
        self.pages.news.changed.emit("global")
        self.wait(
            lambda: (
                "Second" in self.js("document.getElementById('news').textContent", tab)
            )
        )
        self.assertEqual(
            self.js("document.querySelector('#query').value", tab),
            "my unfinished search",
        )
        self.assertEqual(self.js("window.scrollY", tab), 0)
