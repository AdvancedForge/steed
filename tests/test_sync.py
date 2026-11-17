"""Sync through a shared folder: two computers, deletions, conflicts, passwords."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import nacl.pwhash
import nacl.utils
from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import application, keep_directory

from browser import password_vault as pv
from browser import sync
from browser.reading_list import ReadingList

FAST = (
    nacl.pwhash.argon2id.OPSLIMIT_INTERACTIVE,
    nacl.pwhash.argon2id.MEMLIMIT_INTERACTIVE,
)


class Memory:
    """A collection held in a dict, standing in for a profile's bookmarks."""

    def __init__(self, **found):
        self.found = dict(found)
        self.collection = sync.Collection(
            lambda: dict(self.found), self.write, lambda data: data
        )

    def write(self, found):
        self.found = dict(found)


class Computer:
    def __init__(self, folder, device, **found):
        self.folder, self.device = folder, device
        self.items = Memory(**found)

    def sync(self, now):
        return sync.sync_folder(
            self.folder,
            self.device,
            self.device,
            {"profiles": {"global": {"bookmarks": self.items.collection}}},
            now=now,
        )


class FakeKeys:
    def __init__(self):
        self.value = nacl.utils.random(32)

    def key(self, create=True):
        return self.value


class MergeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)

    def test_additions_changes_and_deletions_reach_the_other_computer(self):
        a = Computer(self.folder, "a" * 32, x="one")
        b = Computer(self.folder, "b" * 32, y="two")
        a.sync(100)
        result = b.sync(101)
        self.assertEqual(b.items.found, {"x": "one", "y": "two"})
        self.assertEqual(result["devices"], ["a" * 32])
        a.sync(102)
        self.assertEqual(a.items.found, {"x": "one", "y": "two"})
        b.items.found.pop("x")
        b.items.found["y"] = "changed"
        b.sync(200)
        a.sync(201)
        self.assertEqual(a.items.found, {"y": "changed"})
        # Re-adding after a deletion wins over the old marker.
        a.items.found["x"] = "back"
        a.sync(300)
        b.sync(301)
        self.assertEqual(b.items.found, {"x": "back", "y": "changed"})

    def test_the_newest_change_wins_a_conflict(self):
        a = Computer(self.folder, "a" * 32, x="one")
        b = Computer(self.folder, "b" * 32)
        a.sync(100)
        b.sync(101)
        a.items.found["x"] = "from a"
        b.items.found["x"] = "from b"
        a.sync(200)
        b.sync(250)
        a.sync(260)
        self.assertEqual(a.items.found, {"x": "from b"})
        self.assertEqual(b.items.found, {"x": "from b"})

    def test_a_first_sync_never_overwrites_newer_changes(self):
        a = Computer(self.folder, "a" * 32, x="new", gone="kept elsewhere")
        a.sync(100)
        a.items.found.pop("gone")
        a.sync(150)
        # A computer joining later with old copies of both items.
        c = Computer(self.folder, "c" * 32, x="old", gone="kept elsewhere", z="mine")
        c.sync(500)
        self.assertEqual(c.items.found, {"x": "new", "z": "mine"})
        a.sync(501)
        self.assertEqual(a.items.found, {"x": "new", "z": "mine"})

    def test_unchanged_sync_does_not_rewrite_and_old_markers_go(self):
        a = Computer(self.folder, "a" * 32, x="one")
        a.sync(100)
        path = self.folder / "steed-sync" / f"device-{'a' * 32}.json"
        before = path.read_text()
        a.sync(200)
        self.assertEqual(path.read_text(), before)
        a.items.found.clear()
        a.sync(300)
        records = json.loads(path.read_text())["profiles"]["global"]["bookmarks"]
        self.assertEqual(records["x"], {"m": 300, "d": None})
        a.sync(300 + sync.TOMBSTONE_SECONDS + 1)
        records = json.loads(path.read_text())["profiles"]["global"]["bookmarks"]
        self.assertEqual(records, {})

    def test_damaged_or_foreign_files_and_a_missing_folder(self):
        a = Computer(self.folder, "a" * 32, x="one")
        (self.folder / "steed-sync").mkdir()
        (self.folder / "steed-sync" / "device-bad.json").write_text("{nope")
        (self.folder / "steed-sync" / "device-odd.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "device": "odd",
                    "profiles": {"global": {"bookmarks": {"x": "not a record"}}},
                }
            )
        )
        a.sync(100)
        self.assertEqual(a.items.found, {"x": "one"})
        with self.assertRaises(OSError):
            Computer(self.folder / "unplugged", "a" * 32).sync(100)


class PasswordTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.folder = self.root / "shared"
        self.folder.mkdir()

    def vault(self, name):
        vault = pv.PasswordVault(self.root / name / "passwords.vault", FakeKeys())
        vault.unlock()
        return vault

    def run_sync(self, device, vault, key, now):
        return sync.sync_folder(
            self.folder,
            device,
            device,
            {"profiles": {"global": {}}, "vaults": {"global": vault}},
            key=key,
            now=now,
        )

    def test_logins_travel_sealed_and_only_with_the_passphrase(self):
        key = sync.passphrase_key(self.folder, "correct horse", *FAST)
        with self.assertRaises(pv.VaultError):
            sync.passphrase_key(self.folder, "wrong horse!", *FAST)
        self.assertEqual(sync.passphrase_key(self.folder, "correct horse"), key)
        with self.assertRaises(pv.VaultError):
            sync.passphrase_key(self.folder, "short")
        a, b = self.vault("a"), self.vault("b")
        a.save("https://example.com/login", "ana", "s3cret-synthetic")
        b.save("https://other.example", "bo", "other-synthetic")
        self.run_sync("a" * 32, a, key, 100)
        self.run_sync("b" * 32, b, key, 101)
        self.run_sync("a" * 32, a, key, 102)
        for vault in (a, b):
            found = {(x.origin, x.username, x.password) for x in vault.logins()}
            self.assertEqual(
                found,
                {
                    ("https://example.com", "ana", "s3cret-synthetic"),
                    ("https://other.example", "bo", "other-synthetic"),
                },
            )
        for path in (self.folder / "steed-sync").iterdir():
            text = path.read_text()
            self.assertNotIn("synthetic", text)
            self.assertNotIn("example.com", text)
        # Ids stay local; deleting reaches the other computer.
        a.delete(next(x.id for x in a.logins() if x.username == "bo"))
        self.run_sync("a" * 32, a, key, 200)
        self.run_sync("b" * 32, b, key, 201)
        self.assertEqual([x.username for x in b.logins()], ["ana"])
        # A locked vault keeps its sealed section until it can be opened.
        b.lock()
        own = self.folder / "steed-sync" / f"device-{'b' * 32}.json"
        sealed = json.loads(own.read_text())["profiles"]["global"]["passwords"]
        self.run_sync("b" * 32, b, key, 300)
        self.assertEqual(
            json.loads(own.read_text())["profiles"]["global"]["passwords"], sealed
        )
        # Without the key, nothing is read or written.
        c = self.vault("c")
        self.run_sync("c" * 32, c, nacl.utils.random(32), 400)
        self.assertEqual(c.logins(), [])


class PasskeySyncTests(PasswordTests):
    def test_passkeys_travel_sealed_and_keep_their_local_use(self):
        from browser import passkeys

        key = sync.passphrase_key(self.folder, "correct horse", *FAST)
        a, b = self.vault("a"), self.vault("b")
        record, _ = passkeys.register(
            {"challenge": "AQ", "user": {"id": "AQ", "name": "ana-synthetic"}},
            "https://example.com",
            "example.com",
        )
        a.save_passkey(record)
        a.touch_passkey(record["id"])
        self.run_sync("a" * 32, a, key, 100)
        self.run_sync("b" * 32, b, key, 101)
        (copied,) = b.passkeys()
        self.assertEqual(copied["key"], record["key"])
        self.assertEqual(copied["user_name"], "ana-synthetic")
        self.assertEqual(copied["last_used"], 0.0)  # Used on a, not here.
        for path in (self.folder / "steed-sync").iterdir():
            text = path.read_text()
            self.assertNotIn(record["key"], text)
            self.assertNotIn("ana-synthetic", text)
        # Using it here changes nothing to sync; deleting it does.
        own = self.folder / "steed-sync" / f"device-{'b' * 32}.json"
        before = own.read_text()
        b.touch_passkey(record["id"])
        self.run_sync("b" * 32, b, key, 200)
        self.assertEqual(own.read_text(), before)
        b.delete_passkey(record["id"])
        self.run_sync("b" * 32, b, key, 300)
        self.run_sync("a" * 32, a, key, 301)
        self.assertEqual(a.passkeys(), [])


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        root = Path(self.directory.name)
        self.folder = root / "shared"
        self.folder.mkdir()
        self.home = patch("pathlib.Path.home", return_value=root)
        self.home.start()
        from browser.browser_window import BrowserWindow

        self.window = BrowserWindow()
        self.registry = self.window.profile_manager.registry
        self.service = self.registry.sync
        self.service.key = sync.SyncKey(keyring=False)

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def test_a_profile_syncs_with_another_computer_through_the_dialog(self):
        from browser.tab_manager import TabGroup
        from ui.sync_dialog import SyncDialog

        name = self.window.profile_manager.current_profile_name
        pages = self.registry.start_pages
        pages.add_bookmark(name, "https://here.example/", "Here")
        directory = self.window.tab_manager.profile_dir
        ReadingList(directory).add("https://read.example/", "Read me")
        self.window.tab_manager.save_closed_group(
            TabGroup("g" * 32, "Trip"), [{"url": "https://trip.example/", "title": "T"}]
        )
        self.registry.config.set(
            "search_keywords", {"mdn": ["MDN", "https://developer.mozilla.org/?q={q}"]}
        )
        dialog = SyncDialog(self.service, self.window)
        try:
            self.assertFalse(dialog.now.isEnabled())
            dialog.set_folder(str(self.folder))
            self.assertIn("Synced", dialog.status.text())
            self.assertTrue(dialog.now.isEnabled())
            # Another computer adds a bookmark and a keyword and drops the group.
            other = Memory()
            groups = Memory()
            keywords = Memory()
            sync.sync_folder(
                self.folder,
                "f" * 32,
                "laptop",
                {
                    "global": {"search_keywords": keywords.collection},
                    "profiles": {
                        name: {
                            "bookmarks": other.collection,
                            "saved_groups": groups.collection,
                        }
                    },
                },
                now=time.time() + 5,
            )
            self.assertIn("https://here.example/", other.found)
            self.assertIn("mdn", keywords.found)
            self.assertIn("g" * 32, groups.found)
            other.found["https://there.example/"] = {
                "url": "https://there.example/",
                "title": "There",
                "folder": "",
            }
            groups.found.clear()
            keywords.found["wiki"] = ["Wiki", "https://wiki.example/?q={q}"]
            sync.sync_folder(
                self.folder,
                "f" * 32,
                "laptop",
                {
                    "global": {"search_keywords": keywords.collection},
                    "profiles": {
                        name: {
                            "bookmarks": other.collection,
                            "saved_groups": groups.collection,
                        }
                    },
                },
                now=time.time() + 10,
            )
            dialog.sync_now()
            self.assertIn("laptop", dialog.status.text())
            urls = {b["url"] for b in pages.state(name)["bookmarks"]}
            self.assertEqual(urls, {"https://here.example/", "https://there.example/"})
            self.assertEqual(self.window.tab_manager.saved_groups(), [])
            self.assertIn("wiki", self.registry.config.get("search_keywords"))
            self.assertEqual(
                [e["url"] for e in ReadingList(directory).entries()],
                ["https://read.example/"],
            )
            dialog.passphrase.setText("short")
            dialog.sync_passwords()
            self.assertIn("8 characters", dialog.status.text())
            dialog.set_folder("")
            self.assertIsNone(self.service.folder)
            self.assertFalse(self.service.timer.isActive())
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
