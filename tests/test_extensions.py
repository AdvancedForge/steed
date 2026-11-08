"""Chrome extensions: inspect, install (also from the store), run, sync, restart.

Enabling an extension used to crash Qt WebEngine 6.10/6.11 because profiles
were given their storage folder after creation; these tests enable real ones.
The Chrome Web Store is stood in for by a local server.
"""

import io
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
from urllib.parse import parse_qs, urlsplit

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

import extensions as ext
import webengine
from browser_window import BrowserWindow
from extensions import Extensions, crx_zip, inspect, store_id

STORE_ID = "abcdefghijklmnopabcdefghijklmnop"
MANIFEST = {
    "manifest_version": 3,
    "name": "__MSG_appName__",
    "default_locale": "en",
    "version": "1.0",
    "content_scripts": [{"matches": ["http://127.0.0.1/*"], "js": ["content.js"]}],
    "action": {"default_popup": "popup.html"},
    "permissions": ["storage", "tabs"],
    "icons": {"16": "icon16.png", "48": "icon48.png"},
}
CONTENT = (
    "document.documentElement.dataset.extension = 'ran';"
    "chrome.storage.local.set({seen: 1}).then("
    "() => document.documentElement.dataset.stored = 'yes');"
)


def _png():
    """A 1x1 PNG."""
    import zlib

    def chunk(kind, data):
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x4f\x7c\xff\xff")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


PNG = _png()


def write_extension(folder: Path, name="Steed Test Extension", **changes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps({**MANIFEST, **changes}))
    (folder / "content.js").write_text(CONTENT)
    (folder / "popup.html").write_text("<title>Test Popup</title><p>popup")
    (folder / "icon48.png").write_bytes(PNG)
    (folder / "icon16.png").write_bytes(PNG)
    (folder / "_locales" / "en").mkdir(parents=True, exist_ok=True)
    (folder / "_locales" / "en" / "messages.json").write_text(
        json.dumps({"appName": {"message": name}})
    )
    return folder


def zipped(folder: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for path in folder.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(folder).as_posix())
    return buffer.getvalue()


def crx(folder: Path) -> bytes:
    return b"Cr24" + struct.pack("<II", 3, 4) + b"hdr!" + zipped(folder)


class Pages(BaseHTTPRequestHandler):
    """A page for content scripts, and a stand-in for the store's downloads."""

    store = {}  # id -> .crx bytes

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path == "/crx":
            body = self.store.get(parse_qs(parts.query)["id"][0])
            if body is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        else:
            body = b"<title>Plain</title><p>page"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class InspectTests(unittest.TestCase):
    def test_the_zip_inside_crx_versions_2_and_3(self):
        archive = b"PK\x03\x04rest"
        three = b"Cr24" + struct.pack("<II", 3, 5) + b"hdr!!" + archive
        two = b"Cr24" + struct.pack("<III", 2, 3, 2) + b"keysg" + archive
        self.assertEqual(crx_zip(three), archive)
        self.assertEqual(crx_zip(two), archive)
        for bad in (b"PK\x03\x04", b"Cr24" + struct.pack("<III", 9, 0, 0)):
            with self.assertRaises(ValueError):
                crx_zip(bad)

    def test_store_addresses(self):
        for text in (
            STORE_ID,
            f"https://chromewebstore.google.com/detail/some-name/{STORE_ID}",
            f"https://chromewebstore.google.com/detail/{STORE_ID}?hl=en",
            f"https://chrome.google.com/webstore/detail/x/{STORE_ID}/reviews",
        ):
            self.assertEqual(store_id(text), STORE_ID, text)
        for text in (
            "https://example.com/detail/x/" + STORE_ID,
            "https://chromewebstore.google.com/detail/x/" + "z" * 32,
            "https://chromewebstore.google.com/",
        ):
            self.assertIsNone(store_id(text), text)

    def test_details_from_a_folder_zip_and_crx_before_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = write_extension(root / "x")
            (root / "x.zip").write_bytes(zipped(folder))
            (root / "x.crx").write_bytes(crx(folder))
            for path in (folder, root / "x.zip", root / "x.crx"):
                details = inspect(path)
                self.assertEqual(details["name"], "Steed Test Extension")
                self.assertEqual(details["icon"], PNG)
                self.assertEqual(
                    details["permissions"],
                    [
                        "Read and change pages on 127.0.0.1",
                        "See the addresses and titles of your tabs",
                    ],
                )
                self.assertEqual(details["unsupported"], [])
            everything = write_extension(
                root / "y",
                host_permissions=["<all_urls>"],
                permissions=["storage", "contextMenus", "activeTab", "scripting"],
            )
            self.assertEqual(
                inspect(everything)["permissions"][0],
                "Read and change everything on all websites",
            )
            self.assertEqual(
                inspect(everything)["unsupported"], ["contextMenus", "scripting"]
            )
            old = write_extension(root / "old", manifest_version=2)
            with self.assertRaisesRegex(ValueError, "Manifest V2"):
                inspect(old)
            (root / "junk.zip").write_bytes(b"not a zip")
            for bad in (root / "junk.zip", root / "missing", root):
                with self.assertRaises(ValueError):
                    inspect(bad)


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

    def test_extensions_come_back_as_left_and_none_is_fetched_again(self):
        temporary = tempfile.TemporaryDirectory()
        keep_directory(temporary)  # The engine may still be writing there.
        root = Path(temporary.name)
        first = write_extension(root / "first", name="First")
        second = write_extension(root / "second", name="Second")

        def start():
            profile = webengine.stored_profile(
                "restart-test",
                None,
                storage=root / "storage",
                cache=root / "cache",
                keep_session_cookies=False,
            )
            extensions = Extensions()
            fetched = []
            extensions.download = lambda *a, **k: fetched.append(a)
            extensions.attach("p", profile, root)
            return profile, extensions, fetched

        profile, extensions, _ = start()
        extensions.install("p", first)
        extensions.install("p", second, store=STORE_ID, enabled=False)
        self.wait(lambda: len(extensions.installed("p")) == 2)
        states = {e["name"]: e["enabled"] for e in extensions.installed("p")}
        self.assertEqual(states, {"First": True, "Second": False})
        sip.delete(extensions)
        sip.delete(profile)
        profile, extensions, fetched = start()
        try:
            self.wait(
                lambda: (
                    {e["name"]: e["enabled"] for e in extensions.installed("p")}
                    == {"First": True, "Second": False}
                )
            )
            time.sleep(0.3)
            self.app.processEvents()
            self.assertEqual(fetched, [])
            self.assertEqual(
                ext.synced(root), {STORE_ID: {"name": "Second", "enabled": False}}
            )
        finally:
            sip.delete(extensions)
            sip.delete(profile)


class EngineApiTests(Waiting, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_the_engine_gives_extensions_the_apis_steed_lists(self):
        """When Qt WebEngine changes, ENGINE_APIS (shown to people) must too."""
        temporary = tempfile.TemporaryDirectory()
        keep_directory(temporary)
        root = Path(temporary.name)
        folder = root / "probe"
        folder.mkdir()
        (folder / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": 3,
                    "name": "API probe",
                    "version": "1",
                    "permissions": sorted(ext.API_PERMISSIONS),
                    "action": {"default_popup": "p.html"},
                }
            )
        )
        (folder / "p.html").write_text('<script src="p.js"></script>')
        (folder / "p.js").write_text(
            "document.title = JSON.stringify(Object.keys(chrome).sort());"
        )
        profile = webengine.stored_profile(
            "api-test",
            None,
            storage=root / "storage",
            cache=root / "cache",
            keep_session_cookies=False,
        )
        extensions = Extensions()
        extensions.attach("p", profile, root)
        page = webengine.Page(profile)
        try:
            extensions.install("p", folder)
            self.wait(lambda: extensions.popups("p"))
            page.setUrl(QUrl(extensions.popups("p")[0]["popup"]))
            self.wait(lambda: page.title().startswith("["))
            found = set(json.loads(page.title())) & ext.API_PERMISSIONS
            self.assertEqual(found, ext.ENGINE_APIS)
        finally:
            sip.delete(page)
            sip.delete(extensions)
            sip.delete(profile)


class WindowTests(Waiting, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Pages)
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
        self.store = patch.object(
            ext, "STORE_DOWNLOAD", self.url + "crx?id={id}&v={version}"
        )
        self.store.start()
        self.confirm = patch("ui.extension_install.confirm", return_value=True)
        self.asked = self.confirm.start()
        Pages.store = {}
        self.window = BrowserWindow()
        self.window.show()
        self.name = self.window.profile_manager.current_profile_name
        self.extensions = self.window.profile_manager.registry.extensions

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.confirm.stop()
        self.store.stop()
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
        archive.write_bytes(zipped(write_extension(self.root / "source")))
        dialog = ExtensionsDialog(self.window)
        try:
            self.asked.return_value = False
            dialog.install(archive)
            self.assertEqual(dialog.status.text(), "Not added.")
            self.asked.return_value = True
            dialog.install(self.root / "missing-folder")
            self.assertIn("Choose", dialog.status.text())
            dialog.install(archive)
            self.wait(lambda: dialog.entries and dialog.entries[0]["enabled"])
            self.assertIn("(on)", dialog.list.item(0).text())
            self.assertFalse(dialog.list.item(0).icon().isNull())
            self.assertIn("titles of your tabs", dialog.about.text())
            self.assertIn("a file on this computer", dialog.about.text())
            tab = self.window._new_tab(url=self.url, return_tab=True)
            self.wait(lambda: tab.title() == "Plain" and not tab.page().isLoading())
            self.wait(lambda: self.marker(tab) == "ran yes")
            self.assertTrue(self.window.address_bar.extensions_btn.isVisible())
            self.window._show_extensions_menu()
            menu = self.window.extensions_popup_menu
            labels = [a.text() for a in menu.actions()]
            self.assertIn("Steed Test Extension", labels)
            self.assertIn("Browse Chrome Web Store", labels)
            from ui.extension_popup import ExtensionPopup

            # As a click does: the action fires, then the menu closes.
            next(a for a in menu.actions() if a.text() == labels[0]).trigger()
            menu.close()
            self.wait(lambda: self.window.findChildren(ExtensionPopup))
            (popup,) = self.window.findChildren(ExtensionPopup)
            self.wait(lambda: popup.isVisible() and popup.page.title() == "Test Popup")
            popup.close()
            dialog.toggle()
            self.assertIn("(off)", dialog.list.item(0).text())
            self.assertEqual(self.extensions.popups(self.name), [])
            tab.page().triggerAction(webengine.QWebEnginePage.WebAction.Reload)
            self.wait(lambda: not tab.page().isLoading())
            time.sleep(0.3)
            self.assertEqual(self.marker(tab), "undefined undefined")
            dialog.remove()
            self.wait(lambda: dialog.entries == [])
            self.assertFalse(self.extensions.available("search"))
        finally:
            dialog.deleteLater()

    def test_add_from_the_store_page_bar_and_follow_sync(self):
        import sync

        Pages.store = {STORE_ID: crx(write_extension(self.root / "store"))}
        page = QUrl(f"https://chromewebstore.google.com/detail/test/{STORE_ID}")
        bar = self.window.store_bar
        bar.update_for(page)
        self.assertFalse(bar.isHidden())
        self.assertTrue(bar.add.isVisibleTo(bar))
        bar.update_for(QUrl("https://example.com/"))
        self.assertTrue(bar.isHidden())
        bar.update_for(page)
        bar.add.click()
        self.wait(lambda: self.extensions.from_store(self.name, STORE_ID))
        self.asked.assert_called_once()
        self.assertEqual(self.asked.call_args[0][1]["name"], "Steed Test Extension")
        self.wait(lambda: "is added" in bar.label.text())
        self.assertFalse(bar.add.isVisibleTo(bar))
        self.assertFalse(self.window.extension_installer.add_store(STORE_ID))
        # An id the store does not have (page loads may replace the message).
        seen = []
        self.window.status_bar.messageChanged.connect(seen.append)
        self.assertTrue(self.window.extension_installer.add_store("p" * 32))
        self.wait(lambda: any("no such" in message for message in seen))
        # Sync switches it off, then removes it, as another computer did.
        folder = self.extensions.folder(self.name)
        ext.set_synced(folder, {STORE_ID: {"name": "x", "enabled": False}})
        self.extensions.reconcile(self.name)
        self.assertFalse(self.extensions.from_store(self.name, STORE_ID)["enabled"])
        ext.set_synced(folder, {})
        self.extensions.reconcile(self.name)
        self.wait(lambda: self.extensions.installed(self.name) == [])
        # ...and installs one another computer added, without asking.
        self.asked.reset_mock()
        collections = sync.profile_collections(
            self.window.profile_manager.registry, self.name, folder
        )
        collections["extensions"].write({STORE_ID: {"name": "x", "enabled": True}})
        self.wait(lambda: self.extensions.from_store(self.name, STORE_ID))
        self.asked.assert_not_called()
        self.assertEqual(
            collections["extensions"].read(),
            {STORE_ID: {"name": "Steed Test Extension", "enabled": True}},
        )


if __name__ == "__main__":
    unittest.main()
