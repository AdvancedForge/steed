"""Page translation: only text goes out, in place, reversible; or Google's proxy."""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import page_translation, webengine
from browser.browser_window import BrowserWindow
from ui.prompts import MessageBox

PAGE = (
    b"<title>page</title><p id=a>Hello there friend</p><ul><li>Second line</li></ul>"
    b"<code id=c>keep this code</code><input id=i value='form value'>"
    b"<script>window.marker = 'script text';</script>"
)


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Set-Cookie", "session=SECRET; Path=/")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


class Translator(BaseHTTPRequestHandler):
    requests = []
    broken = False

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"body": body, "headers": dict(self.headers)})
        if type(self).broken:
            self.send_response(500)
            self.end_headers()
            return
        answer = json.dumps({"translatedText": [t.upper() for t in body["q"]]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    def log_message(self, *args):
        pass


class TranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        cls.service = ThreadingHTTPServer(("127.0.0.1", 0), Translator)
        for server in (cls.site, cls.service):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.site.server_port}"
        cls.service_url = f"http://localhost:{cls.service.server_port}/translate"

    @classmethod
    def tearDownClass(cls):
        for server in (cls.site, cls.service):
            server.shutdown()
            server.server_close()

    def setUp(self):
        Translator.requests = []
        Translator.broken = False
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.show()
        self.window.config.set("translate_language", "de")
        self.tab = self.window._new_tab(url=self.origin + "/", return_tab=True)
        self.wait(
            lambda: self.tab.title() == "page" and not self.tab.page().isLoading()
        )

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

    def js(self, source):
        out = []
        webengine.run_script(
            self.tab.page(), source, out.append, world=webengine.PAGE_WORLD
        )
        self.wait(lambda: out)
        return out[0]

    def text(self, element):
        return self.js(f"document.getElementById('{element}').textContent")

    def test_only_text_is_sent_and_written_back_reversibly(self):
        self.window.config.set("translate_service", self.service_url)
        self.window.translate_page()
        self.wait(lambda: self.text("a") == "HELLO THERE FRIEND")
        self.assertEqual(
            self.js("document.querySelector('li').textContent"), "SECOND LINE"
        )
        self.assertEqual(self.text("c"), "keep this code")
        self.assertEqual(self.js("document.getElementById('i').value"), "form value")
        self.assertEqual(self.js("window.marker"), "script text")
        (request,) = Translator.requests
        self.assertEqual(request["body"]["target"], "de")
        self.assertIn("Hello there friend", request["body"]["q"])
        headers = {k.lower(): v for k, v in request["headers"].items()}
        self.assertNotIn("cookie", headers)
        self.assertNotIn("referer", headers)
        self.assertNotIn(self.origin, json.dumps(request))
        self.assertTrue(self.tab.translated)
        self.window.translate_page()
        self.wait(lambda: self.text("a") == "Hello there friend")
        self.assertFalse(self.tab.translated)

    def test_a_failing_service_leaves_the_page_unchanged(self):
        Translator.broken = True
        self.window.config.set("translate_service", self.service_url)
        job = self.window.translate_page()
        messages = []
        job.failed.connect(messages.append)
        self.wait(lambda: messages)
        self.assertIn("did not answer", messages[0])
        self.assertEqual(self.text("a"), "Hello there friend")

    def test_without_a_service_google_translate_is_offered(self):
        with patch.object(
            MessageBox, "question", return_value=MessageBox.StandardButton.No
        ):
            self.assertIsNone(self.window.translate_page())
        with patch.object(
            MessageBox, "question", return_value=MessageBox.StandardButton.Yes
        ):
            tab = self.window.translate_page()
        url = tab.url()
        self.assertEqual(url.host(), "translate.google.com")
        self.assertIn("tl=de", url.query())
        from PyQt6.QtCore import QUrlQuery

        self.assertEqual(QUrlQuery(url).queryItemValue("u"), self.origin + "/")

    def test_settings_are_checked(self):
        page_translation.check_settings("", "")
        page_translation.check_settings("https://t.example/translate", "pt-BR")
        for service, language in (("ftp://x", ""), ("", "english"), ("", "e")):
            with self.assertRaises(ValueError):
                page_translation.check_settings(service, language)
        self.assertEqual(
            list(page_translation.batches(["a" * 3000, "b" * 3000, "c"])),
            [(0, ["a" * 3000]), (1, ["b" * 3000, "c"])],
        )


if __name__ == "__main__":
    unittest.main()
