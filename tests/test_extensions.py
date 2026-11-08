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

import extension_build as eb
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
    versions = {}  # id -> the version an update check offers

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path == "/update":
            extension_id = parse_qs(parts.query)["id"][0]
            offered = self.versions.get(extension_id)
            check = (
                f'<updatecheck status="ok" version="{offered}" codebase="x"/>'
                if offered
                else '<updatecheck status="noupdate"/>'
            )
            body = (
                '<?xml version="1.0"?><gupdate xmlns="http://www.google.com/update2/'
                f'response" protocol="2.0"><app appid="{extension_id}" status="ok">'
                f"{check}</app></gupdate>"
            ).encode()
        elif parts.path == "/crx":
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


def _varint(value):
    out = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        out += bytes([byte | (0x80 if value else 0)])
        if not value:
            return out


def _field(number, data):
    return _varint(number << 3 | 2) + _varint(len(data)) + data


def signed_crx(folder: Path, key: bytes) -> bytes:
    """A .crx whose header carries a key and the id it declares, as the store's."""
    declared = bytes.fromhex(
        "".join("%x" % (ord(c) - ord("a")) for c in eb.extension_id(key))
    )
    header = _field(2, _field(1, key) + _field(2, b"sig")) + _field(
        10000, _field(1, declared)
    )
    return b"Cr24" + struct.pack("<II", 3, len(header)) + header + zipped(folder)


class BuildTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_ids_come_from_keys_and_stay(self):
        key = eb.new_key()
        crx = signed_crx(write_extension(self.root / "x"), key)
        keys, declared = eb.crx_header(crx)
        self.assertIn(key, keys)
        self.assertEqual(declared, eb.extension_id(key))
        for name, expected in (("a", eb.extension_id(key)), ("b", None)):
            folder = self.root / name
            eb.unpack(
                self.root / "x" if name == "b" else _write(self.root, crx), folder
            )
            made = eb.ensure_key(folder, crx if name == "a" else b"")
            if expected:
                self.assertEqual(made, expected)
            # Asking again gives the same id: the key is in the manifest now.
            self.assertEqual(eb.ensure_key(folder), made)
            self.assertRegex(made, "^[a-p]{32}$")
        updated = self.root / "update"
        write_extension(updated)
        eb.keep_key(self.root / "b", updated)
        self.assertEqual(eb.ensure_key(updated), eb.ensure_key(self.root / "b"))

    def test_unsafe_archives_are_refused(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("manifest.json", json.dumps(MANIFEST))
            bundle.writestr("../escape.js", "x")
        archive = self.root / "bad.zip"
        archive.write_bytes(buffer.getvalue())
        with self.assertRaisesRegex(ValueError, "unsafe"):
            eb.unpack(archive, self.root / "out")
        self.assertFalse((self.root / "escape.js").exists())

    def test_narrowing_to_sites(self):
        sites = ["example.com", "news.org"]
        self.assertEqual(
            eb.narrow(["<all_urls>"], sites),
            ["*://*.example.com/*", "*://*.news.org/*"],
        )
        self.assertEqual(
            eb.narrow(["https://www.example.com/a/*", "https://other.net/*"], sites),
            ["https://www.example.com/a/*"],
        )
        self.assertEqual(
            eb.narrow(["*://*.example.com/*"], ["sub.example.com"]),
            ["*://*.sub.example.com/*"],
        )
        manifest = eb.limit_to_sites(
            {
                "host_permissions": ["<all_urls>"],
                "permissions": ["storage", "https://*/*"],
                "content_scripts": [
                    {"matches": ["https://other.net/*"], "js": ["a.js"]},
                    {"matches": ["<all_urls>"], "js": ["b.js"]},
                ],
            },
            ["example.com"],
        )
        self.assertEqual(manifest["host_permissions"], ["*://*.example.com/*"])
        self.assertEqual(
            manifest["permissions"], ["storage", "https://*.example.com/*"]
        )
        self.assertEqual([c["js"] for c in manifest["content_scripts"]], [["b.js"]])
        self.assertEqual(eb.clean_site("https://www.Example.com:8080/a"), "example.com")
        self.assertIsNone(eb.clean_site("not a site!"))

    def test_the_built_copy_runs_the_stand_ins_first(self):
        source = write_extension(self.root / "plain")
        (source / "p.html").write_text("<html><head><title>x</title></head>")
        eb.build(source, self.root / "built")
        built = eb.read_manifest(self.root / "built")
        self.assertEqual(built["background"], {"service_worker": eb.BACKGROUND_NAME})
        self.assertEqual(
            (self.root / "built" / eb.BACKGROUND_NAME).read_text(),
            f'importScripts("/{eb.SHIM_NAME}");\n',
        )
        self.assertEqual(built["content_scripts"][0]["js"][0], eb.SHIM_NAME)
        page = (self.root / "built" / "p.html").read_text()
        self.assertTrue(page.startswith(f'<html><head><script src="/{eb.SHIM_NAME}">'))
        self.assertTrue((self.root / "built" / eb.SHIM_NAME).is_file())
        for kind, expected in (
            ("module", 'import "/steed-shim.js";\nimport "/bg.js";\n'),
            (None, 'importScripts("/steed-shim.js", "/bg.js");\n'),
        ):
            folder = write_extension(
                self.root / f"worker-{kind}",
                background={
                    "service_worker": "bg.js",
                    **({"type": kind} if kind else {}),
                },
                content_scripts=[
                    {"matches": ["<all_urls>"], "js": ["m.js"], "world": "MAIN"}
                ],
            )
            eb.build(folder, self.root / f"built-{kind}")
            self.assertEqual(
                (self.root / f"built-{kind}" / eb.BACKGROUND_NAME).read_text(), expected
            )
            built = eb.read_manifest(self.root / f"built-{kind}")
            self.assertEqual(built["content_scripts"][0]["js"], ["m.js"])
        # The source itself is left as it was.
        self.assertNotIn("background", eb.read_manifest(source))


def _write(root, data):
    path = root / "signed.crx"
    path.write_bytes(data)
    return path


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
        # Straight through Qt: Steed's build would add its stand-ins.
        manager = profile.extensionManager()
        done = []
        # The info a signal passes is only valid during the call: keep the id.
        manager.installFinished.connect(lambda info: done.append(info.id()))
        page = webengine.Page(profile)
        try:
            manager.installExtension(str(folder))
            self.wait(lambda: done)
            (info,) = [i for i in manager.extensions() if i.id() == done[0]]
            manager.setExtensionEnabled(info, True)
            page.setUrl(info.actionPopupUrl())
            self.wait(lambda: page.title().startswith("["))
            found = set(json.loads(page.title())) & ext.API_PERMISSIONS
            self.assertEqual(found, ext.ENGINE_APIS)
        finally:
            sip.delete(page)
            sip.delete(profile)


class WindowBase(Waiting, unittest.TestCase):
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
        self.updates = patch.object(
            ext, "STORE_UPDATE", self.url + "update?id={id}&v={current}&p={version}"
        )
        self.updates.start()
        Pages.versions = {}
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
        self.updates.stop()
        self.store.stop()
        self.home.stop()

    def marker(self, tab):
        return self.evaluate(
            tab.page(),
            "document.documentElement.dataset.extension + ' '"
            " + document.documentElement.dataset.stored",
        )


class WindowTests(WindowBase):
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


SHIM_BACKGROUND = """
chrome.contextMenus.create({ id: "x", title: "x" });
chrome.action.setBadgeText({ text: "1" });
const wait = (ms) => new Promise((done) => setTimeout(done, ms));
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message !== "hello") return;
  (async () => {
    await chrome.storage.sync.set({ a: 1 });
    const synced = await chrome.storage.sync.get("a");
    const local = await chrome.storage.local.get(null);
    let listed = false;
    for (let i = 0; i < 40 && !listed; i++) {
      listed = (await chrome.tabs.query({})).some((tab) => tab.id === sender.tab.id);
      if (!listed) await wait(50);
    }
    const pong = await chrome.tabs.sendMessage(sender.tab.id, "ping");
    respond({
      frame: sender.frameId,
      sync: synced.a,
      leaked: Object.keys(local),
      listed,
      pong,
      allowed: await chrome.permissions.contains({ permissions: ["storage"] }),
      refused: await chrome.permissions.contains({ permissions: ["history"] }),
    });
  })();
  return true;
});
"""
SHIM_CONTENT = """
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message === "ping") respond("pong");
});
chrome.runtime.sendMessage("hello").then((reply) => {
  document.documentElement.dataset.shim = JSON.stringify(reply);
});
"""
SHIM_POPUP = """
chrome.tabs.query({}).then((tabs) => {
  document.title = "tabs:" + tabs.length + " " + tabs.map((tab) => tab.url).join(" ");
});
chrome.storage.local.get("seen").then((items) => {
  document.documentElement.dataset.seen = String(items.seen);
});
"""


class StandInTests(WindowBase):
    """The stand-ins for missing Chrome APIs, the toolbar, sites and updates."""

    def add(self, folder):
        self.extensions.install(self.name, folder)
        self.wait(lambda: self.extensions.installed(self.name))
        return self.extensions.installed(self.name)[0]

    def open(self, url):
        tab = self.window._new_tab(url=url, return_tab=True)
        self.wait(lambda: not tab.page().isLoading() and tab.url().toString() == url)
        return tab

    def test_tabs_messages_storage_sync_and_permissions(self):
        folder = write_extension(
            self.root / "shim",
            background={"service_worker": "bg.js"},
            content_scripts=[{"matches": ["http://127.0.0.1/*"], "js": ["c.js"]}],
            action={"default_popup": "p.html"},
        )
        (folder / "bg.js").write_text(SHIM_BACKGROUND)
        (folder / "c.js").write_text(SHIM_CONTENT)
        (folder / "p.html").write_text('<script src="p.js"></script>')
        (folder / "p.js").write_text(SHIM_POPUP)
        entry = self.add(folder)
        tab = self.open(self.url)
        found = []

        def answered():
            found[:] = [
                self.evaluate(tab.page(), "document.documentElement.dataset.shim || ''")
            ]
            return found[0]

        self.wait(answered)
        self.assertEqual(
            json.loads(found[0]),
            {
                "frame": 0,
                "sync": 1,
                "leaked": [],
                "listed": True,
                "pong": "pong",
                "allowed": True,
                "refused": False,
            },
        )
        popup = self.window.open_extension_popup(entry)
        self.wait(lambda: popup.page.title().startswith("tabs:"))
        self.assertIn(self.url, popup.page.title())
        popup.close()

    def test_site_access_narrows_and_keeps_the_extensions_data(self):
        folder = write_extension(
            self.root / "sites",
            content_scripts=[{"matches": ["<all_urls>"], "js": ["content.js"]}],
            action={"default_popup": "p.html"},
        )
        (folder / "p.html").write_text('<script src="p.js"></script>')
        (folder / "p.js").write_text(SHIM_POPUP)
        entry = self.add(folder)
        tab = self.open(self.url)
        self.wait(lambda: self.marker(tab) == "ran yes")
        port = self.server.server_port
        self.extensions.set_sites(self.name, entry["id"], ["localhost"])
        self.wait(
            lambda: (
                self.extensions.installed(self.name)
                and self.extensions.installed(self.name)[0]["sites"] == ["localhost"]
                and self.extensions.installed(self.name)[0]["enabled"]
            )
        )
        (entry,) = self.extensions.installed(self.name)
        self.assertEqual(entry["id"], self.extensions.installed(self.name)[0]["id"])
        blocked = self.open(self.url)
        time.sleep(0.5)
        self.assertEqual(self.marker(blocked), "undefined undefined")
        allowed = self.open(f"http://localhost:{port}/")
        self.wait(lambda: self.marker(allowed) == "ran yes")
        # chrome.storage from before the change is still there.
        popup = self.window.open_extension_popup(entry)
        self.wait(
            lambda: (
                self.evaluate(popup.page, "document.documentElement.dataset.seen || ''")
                == "1"
            )
        )
        popup.close()
        self.extensions.set_sites(self.name, entry["id"], None)
        self.wait(
            lambda: (
                self.extensions.installed(self.name)
                and self.extensions.installed(self.name)[0]["sites"] is None
            )
        )

    def test_pinned_buttons_open_their_popup(self):
        from ui.extension_popup import ExtensionPopup

        entry = self.add(write_extension(self.root / "pin"))
        bar = self.window.address_bar
        self.assertEqual(bar.extension_pins, [])
        self.window._show_extensions_menu()
        menu = self.window.extensions_popup_menu
        pins = next(a for a in menu.actions() if a.text() == "Pinned Buttons").menu()
        pins.actions()[0].setChecked(True)
        menu.close()
        self.wait(lambda: len(bar.extension_pins) == 1)
        self.assertEqual(
            bar.extension_pins[0].toolTip(), entry["name"] + " (Alt+Shift+1)"
        )
        self.assertFalse(bar.extension_pins[0].icon().isNull())
        bar.extension_pins[0].click()
        self.wait(lambda: self.window.findChildren(ExtensionPopup))
        (popup,) = self.window.findChildren(ExtensionPopup)
        self.wait(lambda: popup.page.title() == "Test Popup")
        popup.close()
        self.extensions.set_pinned(self.name, entry["id"], False)
        self.wait(lambda: bar.extension_pins == [])

    def test_updates_keep_the_id_and_wait_when_asking_for_more(self):
        Pages.store = {STORE_ID: crx(write_extension(self.root / "v1"))}
        self.window.extension_installer.add_store(STORE_ID)
        self.wait(lambda: self.extensions.from_store(self.name, STORE_ID))
        first = self.extensions.from_store(self.name, STORE_ID)
        tab = self.open(self.url)
        self.wait(lambda: self.marker(tab) == "ran yes")
        summaries = []
        self.extensions.updates_checked.connect(lambda n, s: summaries.append(s))
        self.extensions.check_updates(self.name)
        self.wait(lambda: summaries)
        self.assertEqual(summaries[-1], "Your extensions are up to date.")

        def version():
            """The installed copy's version, once it is installed and on."""
            entry = self.extensions.from_store(self.name, STORE_ID)
            if not entry or not entry["enabled"]:
                return None
            try:
                return inspect(entry["path"])["version"]
            except ValueError:
                return None  # The old copy, mid-swap: its files are gone.

        v2 = write_extension(
            self.root / "v2", version="2.0", action={"default_popup": "p.html"}
        )
        (v2 / "p.html").write_text('<script src="p.js"></script>')
        (v2 / "p.js").write_text(SHIM_POPUP)
        Pages.store = {STORE_ID: crx(v2)}
        Pages.versions = {STORE_ID: "2.0"}
        self.extensions.check_updates(self.name)
        self.wait(lambda: len(summaries) == 2)
        self.assertIn("Updating 1", summaries[-1])
        self.wait(lambda: version() == "2.0")
        second = self.extensions.from_store(self.name, STORE_ID)
        self.assertEqual(second["id"], first["id"])
        self.assertTrue(second["enabled"])
        self.asked.assert_called_once()  # Only when it was first added.
        # The new version runs: its content script, and its background answers
        # the popup; chrome.storage written by version 1 is still there.
        after = self.open(self.url)
        self.wait(lambda: self.marker(after) == "ran yes")
        popup = self.window.open_extension_popup(second)
        self.wait(lambda: popup.page.title().startswith("tabs:"))
        self.wait(
            lambda: (
                self.evaluate(popup.page, "document.documentElement.dataset.seen || ''")
                == "1"
            )
        )
        popup.close()

        more = write_extension(
            self.root / "v3", version="3.0", permissions=["storage", "tabs", "history"]
        )
        Pages.store = {STORE_ID: crx(more)}
        Pages.versions = {STORE_ID: "3.0"}
        self.extensions.check_updates(self.name)
        self.wait(
            lambda: (self.extensions.from_store(self.name, STORE_ID) or {}).get(
                "update"
            )
        )
        self.assertEqual(version(), "2.0")
        self.assertEqual(
            self.extensions.from_store(self.name, STORE_ID)["update"], "3.0"
        )
        self.extensions.apply_update(self.name, STORE_ID)
        self.wait(lambda: version() == "3.0")
        third = self.extensions.from_store(self.name, STORE_ID)
        self.assertEqual(third["id"], first["id"])
        self.assertEqual(third["update"], "")
        self.assertFalse(self.extensions.waiting_update(self.name, STORE_ID))


BRIDGE_BACKGROUND = """
chrome.contextMenus.removeAll(() => {
  chrome.contextMenus.create({ id: "page", title: "Page item", contexts: ["page"] });
  chrome.contextMenus.create({ id: "sel", title: "Look up %s", contexts: ["selection"] });
});
chrome.action.setBadgeText({ text: "7" });
chrome.action.setBadgeBackgroundColor({ color: [0, 128, 0, 255] });
const mark = (tabId, label) =>
  chrome.scripting.executeScript({
    target: { tabId },
    func: (text) => {
      document.documentElement.dataset.mark = text;
      return document.title;
    },
    args: [label],
  });
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const [done] = await mark(tab.id, "menu:" + info.menuItemId + ":" + (info.selectionText || ""));
  await chrome.storage.local.set({ title: done.result });
});
chrome.action.onClicked.addListener((tab) => mark(tab.id, "action"));
chrome.commands.onCommand.addListener((name, tab) => mark(tab.id, "command:" + name));
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const target = { tabId: sender.tab.id };
  const steps = {
    css: () => chrome.scripting.insertCSS({ target, css: "body { outline: 3px solid red; }" }).then(() => "ok"),
    uncss: () => chrome.scripting.removeCSS({ target, css: "body { outline: 3px solid red; }" }).then(() => "ok"),
    async: () =>
      chrome.scripting
        .executeScript({ target, func: async () => { await new Promise((r) => setTimeout(r, 100)); return 41 + 1; } })
        .then((results) => results[0].result),
    title: () => chrome.storage.local.get("title").then((items) => items.title),
    world: () =>
      chrome.scripting
        .executeScript({
          target,
          func: async () => {
            const reply = await chrome.runtime.sendMessage("worldping");
            await chrome.storage.local.set({ fromWorld: chrome.runtime.id });
            const kept = await chrome.storage.local.get("fromWorld");
            return {
              reply,
              kept: kept.fromWorld,
              url: chrome.runtime.getURL("/x.html"),
              name: chrome.runtime.getManifest().name,
            };
          },
        })
        .then((results) => ({ ...results[0].result, tab: sender.tab.id })),
    listen: () =>
      chrome.scripting
        .executeScript({
          target,
          func: () => {
            chrome.runtime.onMessage.addListener((message, from, respond) => {
              if (message === "toworld") respond("world heard " + from.id.length);
            });
            return true;
          },
        })
        .then(() => chrome.tabs.sendMessage(sender.tab.id, "toworld")),
    register: () =>
      chrome.scripting
        .registerContentScripts([{ id: "reg", matches: ["http://127.0.0.1/*"], js: ["reg.js"], runAt: "document_end" }])
        .then(() => chrome.scripting.getRegisteredContentScripts())
        .then((scripts) => scripts.map((script) => script.id)),
  };
  if (message === "worldping") {
    respond({ tab: sender.tab && sender.tab.id, url: sender.url, frame: sender.frameId });
    return;
  }
  if (!steps[message]) return;
  steps[message]().then(respond, (error) => respond("error: " + error.message));
  return true;
});
"""
BRIDGE_CONTENT = """
window.addEventListener("message", (event) => {
  if (!event.data || !event.data.ask) return;
  chrome.runtime.sendMessage(event.data.ask).then((reply) => {
    document.documentElement.dataset["got" + event.data.ask] = JSON.stringify(reply);
  });
});
"""


class FakeMenuRequest:
    """What a right-click hit, as QWebEngineContextMenuRequest tells it."""

    from PyQt6.QtWebEngineCore import QWebEngineContextMenuRequest

    MediaType = QWebEngineContextMenuRequest.MediaType

    def __init__(self, selection=""):
        self.selection = selection

    def linkUrl(self):
        return QUrl()

    def mediaUrl(self):
        return QUrl()

    def selectedText(self):
        return self.selection

    def mediaType(self):
        return self.MediaType.MediaTypeNone

    def isContentEditable(self):
        return False


class BridgeTests(WindowBase):
    """Menus, badges, scripting and shortcuts, through the bridge page."""

    def test_menus_badges_scripting_and_shortcuts(self):
        from PyQt6.QtWidgets import QMenu

        folder = write_extension(
            self.root / "bridge",
            permissions=["storage", "contextMenus", "scripting"],
            host_permissions=["<all_urls>"],
            background={"service_worker": "bg.js"},
            content_scripts=[{"matches": ["http://127.0.0.1/*"], "js": ["c.js"]}],
            action={"default_title": "Bridge"},
            commands={"say": {"suggested_key": {"default": "Alt+Shift+Y"}}},
        )
        (folder / "bg.js").write_text(BRIDGE_BACKGROUND)
        (folder / "c.js").write_text(BRIDGE_CONTENT)
        (folder / "reg.js").write_text(
            "document.documentElement.dataset.registered = 'yes';"
        )
        self.extensions.install(self.name, folder)
        self.wait(lambda: self.extensions.installed(self.name))
        (entry,) = self.extensions.installed(self.name)
        bridges = self.extensions.bridges
        tab = self.window._new_tab(url=self.url, return_tab=True)
        self.wait(lambda: not tab.page().isLoading() and tab.title() == "Plain")
        self.wait(lambda: bridges.tab_id(self.name, entry["id"], tab) is not None)
        self.wait(lambda: len(bridges.menus(self.name, entry["id"])) == 2)

        def data(key):
            return self.evaluate(
                tab.page(), f"document.documentElement.dataset.{key} || ''"
            )

        def ask(step):
            webengine.run_script(
                tab.page(),
                f"window.postMessage({{ask: '{step}'}}, '*')",
                world=webengine.PAGE_WORLD,
            )
            self.wait(lambda: data("got" + step))
            return json.loads(data("got" + step))

        # Page menu: the page item; with a selection, the selection item.
        menu = QMenu()
        self.window._add_extension_menu_items(menu, tab, FakeMenuRequest())
        labels = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertEqual(labels, ["Page item"])
        next(a for a in menu.actions() if a.text() == "Page item").trigger()
        self.wait(lambda: data("mark") == "menu:page:")
        self.assertEqual(ask("title"), "Plain")
        menu = QMenu()
        self.window._add_extension_menu_items(menu, tab, FakeMenuRequest("steed"))
        (item,) = [a for a in menu.actions() if not a.isSeparator()]
        self.assertEqual(item.text(), "Look up steed")
        item.trigger()
        self.wait(lambda: data("mark") == "menu:sel:steed")

        # The badge, on its pinned button; a click without a popup reaches it.
        self.extensions.set_pinned(self.name, entry["id"], True)
        self.wait(lambda: bridges.badge(self.name, entry["id"], tab).get("text") == "7")
        self.assertEqual(
            bridges.badge(self.name, entry["id"], tab)["color"], "rgba(0, 128, 0, 1)"
        )
        self.wait(lambda: len(self.window.address_bar.extension_pins) == 1)
        self.window.address_bar.extension_pins[0].click()
        self.wait(lambda: data("mark") == "action")

        # Its own shortcut and Alt+Shift+1 for the first pinned button.
        keys = {s.key().toString(): s for s in self.window._extension_shortcuts}
        self.assertIn("Alt+Shift+1", keys)
        keys["Alt+Shift+Y"].activated.emit()
        self.wait(lambda: data("mark") == "command:say")

        # Styles in and out; an async function's value; registered scripts.
        self.assertEqual(ask("css"), "ok")
        outline = "getComputedStyle(document.body).outlineStyle"
        self.wait(lambda: self.evaluate(tab.page(), outline) == "solid")
        self.assertEqual(ask("uncss"), "ok")
        self.wait(lambda: self.evaluate(tab.page(), outline) == "none")
        self.assertEqual(ask("async"), 42)
        # Injected scripts have a chrome object reaching the extension.
        world = ask("world")
        self.assertEqual(world["reply"]["tab"], world["tab"])
        self.assertEqual(world["reply"]["url"], self.url)
        self.assertEqual(world["reply"]["frame"], 0)
        self.assertEqual(world["kept"], entry["id"])
        self.assertEqual(world["url"], f"chrome-extension://{entry['id']}/x.html")
        # As in Chrome, getManifest() gives the manifest as written.
        self.assertEqual(world["name"], "__MSG_appName__")
        # ...and receives tabs.sendMessage, ahead of the content script, which
        # has no listener for it.
        self.assertEqual(ask("listen"), "world heard 32")
        # The page cannot pose as the extension's world without its token.
        asked = dict(bridges.world_asks)
        webengine.run_script(
            tab.page(),
            "console.debug('\\u0001steedworld ' + JSON.stringify({extension: "
            f"'{entry['id']}', token: 'guess', ask: 1, kind: 'message', "
            "message: 'worldping'}))",
            world=webengine.PAGE_WORLD,
        )
        time.sleep(0.2)
        self.app.processEvents()
        self.assertEqual(bridges.world_asks, asked)
        self.assertEqual(ask("register"), ["reg"])
        tab.page().triggerAction(webengine.QWebEnginePage.WebAction.Reload)
        self.wait(lambda: data("registered") == "yes")


if __name__ == "__main__":
    unittest.main()
