"""New Tab photos from your own photos, a folder and favourites; skipping
online ones; black & white."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtGui import QColor, QImage
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow


def picture(path, color, size=(64, 36)):
    image = QImage(*size, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    image.save(str(path))
    return path


class PhotoSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = Path(self.directory.name)
        self.patch = patch("pathlib.Path.home", return_value=self.home)
        self.patch.start()
        self.window = BrowserWindow()
        self.pages = self.window.profile_manager.registry.start_pages
        self.photos = self.pages.photos
        self.folder = self.home / "Pictures"
        self.folder.mkdir()
        for name, color in (
            ("a.png", "#ff0000"),
            ("b.jpg", "#00ff00"),
            ("c.png", "#0000ff"),
        ):
            picture(self.folder / name, color)
        (self.folder / "notes.txt").write_text("not a photo")

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.patch.stop()

    def settle(self, seconds=0.2):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def use(self, **changes):
        settings = {**self.pages.state("global")["settings"], **changes}
        self.pages.configure("global", settings)
        return settings

    def test_a_folder_is_shown_in_a_shuffled_round_each_photo_once(self):
        settings = self.use(photo_source="folder", photo_folder=str(self.folder))
        changed = []
        self.photos.changed.connect(changed.append)
        seen = []
        for _ in range(3):
            self.pages.refresh_photo(settings, now=True)
            current = self.photos.current()
            self.assertEqual(current["source"], "folder")
            seen.append(current["id"])
        self.assertEqual(sorted(seen), ["a.png", "b.jpg", "c.png"])
        self.settle()
        self.assertEqual(len(changed), 3)  # Told after, not while rendering.
        self.pages.refresh_photo(settings, now=True)  # A new round.
        self.assertIn(self.photos.current()["id"], seen)
        # Not due while its time is not up; due at once for another source.
        self.assertFalse(self.photos.due("6h", "folder", str(self.folder)))
        self.assertTrue(self.photos.due("6h", "online"))
        # No credit line for your own photos.
        html = self.pages.render("global", "newtab").decode()
        self.assertNotIn('class="credit"', html)
        self.assertIn("data:image/jpeg;base64,", html)

    def test_added_photos_are_copied_resized_and_used(self):
        big = picture(self.home / "big.png", "#336699", size=(3000, 2000))
        added = self.photos.add_photos(
            [
                big,
                self.folder / "a.png",
                self.folder / "notes.txt",
                self.home / "nope.png",
            ]
        )
        self.assertEqual(added, 2)
        copies = sorted(self.photos.mine_folder.glob("*.jpg"))
        self.assertEqual(len(copies), 2)
        self.assertLessEqual(max(QImage(str(p)).width() for p in copies), 2560)
        settings = self.use(photo_source="mine")
        self.pages.refresh_photo(settings, now=True)
        self.assertEqual(self.photos.current()["source"], "mine")

    def test_favourites_black_and_white_and_never_again(self):
        settings = self.use(
            photo_source="folder",
            photo_folder=str(self.folder),
            photo_style="grayscale",
        )
        self.pages.refresh_photo(settings, now=True)
        stored = QImage(str(self.photos.image_path))
        pixel = stored.pixelColor(10, 10)
        self.assertEqual(pixel.red(), pixel.green())
        self.assertEqual(pixel.green(), pixel.blue())
        self.assertIn("♡ Save<", self.pages.render("global", "newtab").decode())
        self.assertIsNotNone(self.photos.save_favourite())
        self.assertTrue(self.photos.current()["favourite"])
        self.assertEqual(len(list(self.photos.favourites_folder.glob("*.jpg"))), 1)
        html = self.pages.render("global", "newtab").decode()
        self.assertIn("♥ Saved", html)
        self.assertNotIn("Skip this one", html)  # Only online photos are skipped.
        self.assertFalse(self.photos.block_current())
        settings = self.use(photo_source="favourites", photo_style="color")
        self.pages.refresh_photo(settings, now=True)
        self.assertEqual(self.photos.current()["source"], "favourites")
        # An online photo can be ruled out for good.
        self.use(photo_source="online")
        self.photos._write_info(
            {"id": "42", "source": "online", "fetched": time.time(), "author": "A"}
        )
        self.assertTrue(self.photos.block_current())
        self.assertEqual(json.loads(self.photos.blocked_path.read_text()), ["42"])
        self.assertIn("Skip this one", self.pages.render("global", "newtab").decode())

    def test_choices_are_checked(self):
        settings = self.pages.state("global")["settings"]
        for bad in (
            {"photo_source": "web"},
            {"photo_style": "sepia"},
            {"photo_folder": "relative/path"},
            {"photo_folder": 5},
        ):
            with self.assertRaises(ValueError, msg=bad):
                self.pages.configure("global", {**settings, **bad})
        with self.assertRaises(ValueError):  # A folder must be chosen first.
            self.pages.set_from_page("global", [("photo_source", "folder")])
        self.pages.set_from_page("global", [("photo_style", "grayscale")])
        self.assertEqual(
            self.pages.state("global")["settings"]["photo_style"], "grayscale"
        )

    def test_the_panel_and_its_actions(self):
        html = self.pages.render("global", "newtab").decode()
        for link in ("newtab-add-photos", "newtab-folder", "photo_source=online"):
            self.assertIn(link, html)
        self.assertIn("A folder…", html)  # No folder yet: choose one first.
        tab = self.window._new_tab(url="steed:newtab", return_tab=True)
        end = time.monotonic() + WAIT_SECONDS
        while tab.title() != "New Tab" and time.monotonic() < end:
            self.settle(0.05)
        with patch(
            "browser.browser_window.QFileDialog.getExistingDirectory",
            return_value=str(self.folder),
        ):
            self.window._start_page_action(tab, QUrl("steed://action/newtab-folder"))
        settings = self.pages.state("global")["settings"]
        self.assertEqual(
            (settings["photo_source"], settings["photo_folder"]),
            ("folder", str(self.folder)),
        )
        self.assertEqual(self.photos.current()["source"], "folder")
        empty = self.home / "Empty"
        empty.mkdir()
        with patch(
            "browser.browser_window.QFileDialog.getExistingDirectory",
            return_value=str(empty),
        ):
            self.window._start_page_action(tab, QUrl("steed://action/newtab-folder"))
        self.assertEqual(
            self.pages.state("global")["settings"]["photo_folder"], str(self.folder)
        )
        with patch(
            "browser.browser_window.QFileDialog.getOpenFileNames",
            return_value=([str(self.folder / "a.png")], ""),
        ):
            self.window._start_page_action(
                tab, QUrl("steed://action/newtab-add-photos")
            )
        self.assertEqual(self.pages.state("global")["settings"]["photo_source"], "mine")
        self.window._start_page_action(tab, QUrl("steed://action/newtab-favourite"))
        self.assertTrue(self.photos.current()["favourite"])


if __name__ == "__main__":
    unittest.main()


class PhotoServiceDownTests(unittest.TestCase):
    """New photo now while the photo service does not answer (tests point it
    at a closed port): a recent photo instead, and a message either way."""

    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        from services.newtab_photo import NewTabPhotos

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.photos = NewTabPhotos(Path(self.directory.name))
        self.messages, self.changed = [], []
        self.photos.failed.connect(self.messages.append)
        self.photos.changed.connect(self.changed.append)

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def remember(self, name, color):
        image = QImage(64, 36, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        path = Path(self.directory.name) / f"{name}.jpg"
        image.save(str(path), "JPEG")
        data = path.read_bytes()
        self.photos._remember(data, {"id": name, "author": f"Ann {name}"}, color)
        return data

    def test_a_recent_photo_is_shown_and_you_are_told(self):
        shown = self.remember("11", "#ff0000")
        self.remember("22", "#0000ff")
        self.photos._saved(shown, {"id": "11"}, keep=False)
        self.changed.clear()
        self.photos.refresh(now=True)
        self.wait(lambda: self.messages)
        self.assertIn("recent photos", self.messages[0])
        self.assertEqual(self.photos.current()["id"], "22")  # Not the same one.
        self.assertEqual(self.photos.current()["author"], "Ann 22")
        self.assertEqual(self.changed, [False])
        self.assertFalse(self.photos.fetching)

    def test_without_recent_photos_it_says_to_try_later(self):
        self.photos.refresh(now=True)
        self.wait(lambda: self.messages)
        self.assertIn("Try again", self.messages[0])
        self.assertIsNone(self.photos.current())
        # A due refresh nobody asked for fails quietly.
        self.messages.clear()
        self.photos.refresh()
        self.wait(lambda: not self.photos.fetching)
        self.assertEqual(self.messages, [])

    def test_only_the_newest_photos_are_kept(self):
        from services import newtab_photo

        with patch.object(newtab_photo, "RECENT_KEPT", 2):
            for index, name in enumerate(("1", "2", "3")):
                self.remember(name, "#00ff00")
                time.sleep(0.01 * (index + 1))
        kept = sorted(p.stem for p in self.photos.recent_folder.glob("*.jpg"))
        self.assertEqual(kept, ["2", "3"])
        self.assertFalse((self.photos.recent_folder / "1.json").exists())


class CommonsFallbackTests(unittest.TestCase):
    """When Picsum does not answer, a featured landscape from Wikimedia
    Commons (a local stand-in here), credited with its author and licence."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        cls.app = application()
        image = QImage(64, 36, QImage.Format.Format_RGB32)
        image.fill(QColor("#3070c0"))
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        path = Path(folder.name) / "p.jpg"
        image.save(str(path), "JPEG")
        jpeg = path.read_bytes()

        class Commons(BaseHTTPRequestHandler):
            agents = []

            def do_GET(self):
                Commons.agents.append(self.headers.get("User-Agent"))
                if self.path.startswith("/api"):
                    host = f"http://{self.headers['Host']}"
                    pages = {
                        "1": {
                            "pageid": 1,
                            "imageinfo": [
                                {
                                    "mime": "image/jpeg",
                                    "width": 4000,
                                    "height": 2400,
                                    "thumburl": host + "/image.jpg",
                                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Glen.jpg",
                                    "extmetadata": {
                                        "Artist": {
                                            "value": '<a href="x">Ann <b>Lee</b></a>'
                                        },
                                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                    },
                                }
                            ],
                        },
                        "2": {
                            "pageid": 2,
                            "imageinfo": [
                                {  # A panorama: skipped.
                                    "mime": "image/jpeg",
                                    "width": 9000,
                                    "height": 1500,
                                    "thumburl": host + "/image.jpg",
                                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:P.jpg",
                                }
                            ],
                        },
                    }
                    body = json.dumps({"query": {"pages": pages}}).encode()
                else:
                    body = jpeg
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        cls.Commons = Commons
        cls.server = HTTPServer(("127.0.0.1", 0), Commons)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)

    def setUp(self):
        from services import newtab_photo

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        port = self.server.server_port
        # Test-only: the stand-in's thumbnails are http on 127.0.0.1.
        self.host = patch.object(
            newtab_photo, "COMMONS_URL", f"http://127.0.0.1:{port}/api?{{letter}}"
        )
        self.host.start()
        self.addCleanup(self.host.stop)
        self.photos = newtab_photo.NewTabPhotos(Path(self.directory.name))
        self.messages = []
        self.photos.failed.connect(self.messages.append)

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def test_a_commons_photo_is_shown_with_its_credit(self):
        from services import newtab_photo

        with patch.object(newtab_photo, "_trusted_image", lambda url: True):
            self.photos.refresh(now=True)
            self.wait(lambda: not self.photos.fetching)
        found = self.photos.current()
        self.assertEqual(found["id"], "commons:1")
        self.assertEqual(found["author"], "Ann Lee")
        self.assertEqual(found["provider"], "Wikimedia Commons, CC BY-SA 4.0")
        self.assertEqual(
            found["page"], "https://commons.wikimedia.org/wiki/File:Glen.jpg"
        )
        self.assertEqual(self.messages, [])
        self.assertIn("Steed", self.Commons.agents[0])
        # Picsum failed just now: the next photo asks Commons first.
        self.assertGreater(self.photos.picsum_failed, 0)
        self.photos.refresh(now=True)
        self.assertEqual(self.photos.source_now, "commons")
        self.wait(lambda: not self.photos.fetching)

    def test_images_only_come_from_wikimedia_over_https(self):
        from services.newtab_photo import _trusted_image

        self.assertTrue(_trusted_image("https://upload.wikimedia.org/a.jpg"))
        self.assertFalse(_trusted_image("http://upload.wikimedia.org/a.jpg"))
        self.assertFalse(_trusted_image("https://wikimedia.org.evil.example/a.jpg"))
        self.photos.refresh(now=True)  # The stand-in's own (http) images: refused.
        self.wait(lambda: not self.photos.fetching)
        self.assertIsNone(self.photos.current())
        self.assertIn("Try again", self.messages[0])
