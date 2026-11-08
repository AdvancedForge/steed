"""Chrome extensions: install, run, switch off, remember across restarts.

Enabling an extension used to crash Qt WebEngine 6.10/6.11 because profiles
were given their storage folder after creation; these tests enable real ones.
"""

import json
import struct
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

import webengine
from browser_window import BrowserWindow
from extensions import Extensions, crx_zip

MANIFEST = {
    "manifest_version": 3,
    "name": "Steed Test Extension",
    "version": "1.0",
    "content_scripts": [{"matches": ["http://127.0.0.1/*"], "js": ["content.js"]}],
    "action": {"default_popup": "popup.html"},
    "permissions": ["storage"],
}
CONTENT = (
    "document.documentElement.dataset.extension = 'ran';"
    "chrome.storage.local.set({seen: 1}).then("
    "() => document.documentElement.dataset.stored = 'yes');"
)


def write_extension(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(MANIFEST))
    (folder / "content.js").write_text(CONTENT)
    (folder / "popup.html").write_text("<title>Test Popup</title><p>popup")
    return folder


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<title>Plain</title><p>page"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CrxTests(unittest.TestCase):
    def test_the_zip_inside_crx_versions_2_and_3(self):
        archive = b"PK\x03\x04rest"
        three = b"Cr24" + struct.pack("<II", 3, 5) + b"hdr!!" + archive
        two = b"Cr24" + struct.pack("<III", 2, 3, 2) + b"keysg" + archive
        self.assertEqual(crx_zip(three), archive)
        self.assertEqual(crx_zip(two), archive)
        for bad in (b"PK\x03\x04", b"Cr24" + struct.pack("<III", 9, 0, 0)):
            with self.assertRaises(ValueError):
                crx_zip(bad)


class Waiting:
    def wait(self, predicate, seconds=WAIT_SECONDS):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def evaluate(self, page, script):
        result = []
        webengine.run_script(page, script, result.append, world=webengine.PAGE_WORLD)
        self.wait(lambda: result)
        return result[0]


class RestartTests(Waiting, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_switched_on_extensions_are_switched_on_again_after_a_restart(self):
        temporary = tempfile.TemporaryDirectory()
        keep_directory(temporary)  # The engine may still be writing there.
        root = Path(temporary.name)
        source = write_extension(root / "source")

        def start():
            profile = webengine.stored_profile(
                "restart-test",
                None,
                storage=root / "storage",
                cache=root / "cache",
                keep_session_cookies=False,
            )
            extensions = Extensions()
            extensions.attach("p", profile, root)
            return profile, extensions

        profile, extensions = start()
        extensions.install("p", source)
        self.wait(lambda: [e["enabled"] for e in extensions.installed("p")] == [True])
        sip.delete(extensions)
        sip.delete(profile)
        profile, extensions = start()
        try:
            self.wait(
                lambda: [e["enabled"] for e in extensions.installed("p")] == [True]
            )
            (entry,) = extensions.installed("p")
            extensions.set_enabled("p", entry["id"], False)
            self.assertEqual(
                json.loads((root / "extensions.json").read_text()), {"enabled": []}
            )
        finally:
            sip.delete(extensions)
            sip.delete(profile)


class WindowTests(Waiting, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.root = Path(self.directory.name)
        self.home = patch("pathlib.Path.home", return_value=self.root)
        self.home.start()
        self.window = BrowserWindow()
        self.window.show()

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def marker(self, tab):
        return self.evaluate(
            tab.page(),
            "document.documentElement.dataset.extension + ' '"
            " + document.documentElement.dataset.stored",
        )

    def test_install_from_a_zip_run_open_the_popup_and_switch_off(self):
        from ui.extensions_dialog import ExtensionsDialog

        archive = self.root / "test.zip"
        source = write_extension(self.root / "source")
        with zipfile.ZipFile(archive, "w") as bundle:
            for path in source.iterdir():
                bundle.write(path, path.name)
        name = self.window.profile_manager.current_profile_name
        extensions = self.window.profile_manager.registry.extensions
        dialog = ExtensionsDialog(extensions, name, self.window)
        try:
            dialog.install(self.root / "missing-folder")
            self.assertIn("Choose", dialog.status.text())
            dialog.install(archive)
            self.wait(lambda: dialog.entries and dialog.entries[0]["enabled"])
            self.assertIn("(on)", dialog.list.item(0).text())
            tab = self.window._new_tab(url=self.url, return_tab=True)
            self.wait(lambda: tab.title() == "Plain" and not tab.page().isLoading())
            self.wait(lambda: self.marker(tab) == "ran yes")
            self.window._fill_extensions_menu()
            labels = [a.text() for a in self.window.extensions_menu.actions()]
            self.assertIn("Steed Test Extension", labels)
            popup = self.window.open_extension_popup(extensions.popups(name)[0])
            self.wait(lambda: popup.page.title() == "Test Popup")
            popup.close()
            dialog.toggle()
            self.assertIn("(off)", dialog.list.item(0).text())
            self.assertEqual(extensions.popups(name), [])
            tab.page().triggerAction(webengine.QWebEnginePage.WebAction.Reload)
            self.wait(lambda: not tab.page().isLoading())
            time.sleep(0.3)
            self.assertEqual(self.marker(tab), "undefined undefined")
            dialog.remove()
            self.wait(lambda: dialog.entries == [])
            self.assertFalse(extensions.available("search"))
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
