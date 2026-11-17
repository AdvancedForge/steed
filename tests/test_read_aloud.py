"""Read Aloud: the selection or the article, sentence by sentence, with
pause, speed and stop (Qt's mock speech engine here)."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser.browser_window import BrowserWindow
from ui.read_aloud import ReadAloud, chunks

ARTICLE = (
    "<title>Tides</title><article><h1>Tides</h1>"
    + "".join(
        f"<p>Paragraph {n} explains how the moon pulls the sea. "
        "It rises twice a day. Fishermen plan around it.</p>"
        for n in range(8)
    )
    + "</article>"
)


class PieceTests(unittest.TestCase):
    def test_text_becomes_sentences(self):
        self.assertEqual(
            chunks("One two.  Three?\n\nFour!\nFive"),
            ["One two.", "Three?", "Four!", "Five"],
        )
        long = chunks("word " * 300)
        self.assertTrue(all(len(piece) <= 400 for piece in long))
        self.assertEqual(" ".join(long).split(), ["word"] * 300)


class ReadAloudTests(unittest.TestCase):
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
        self.reader = self.window._read_aloud = ReadAloud(self.window, "mock")
        self.spoken = []
        engine = self.reader._engine()
        engine.sayingWord.connect(lambda *a: None)
        original = engine.say
        engine.say = lambda text: (self.spoken.append(text), original(text))

    def tearDown(self):
        self.reader.stop()
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

    def test_reads_the_selection_then_stops(self):
        self.tab.setHtml(
            "<title>Sel</title><p id=a>Read just this. And this.</p><p>Not this.</p>",
            QUrl("https://sel.test/"),
        )
        self.wait(lambda: self.tab.title() == "Sel")
        self.tab.page().runJavaScript(
            "getSelection().selectAllChildren(document.getElementById('a'))"
        )
        self.window.read_aloud()
        self.wait(lambda: not self.reader.active and len(self.spoken) == 2)
        self.assertEqual(self.spoken, ["Read just this.", "And this."])
        self.assertFalse(self.reader.player.isVisible())

    def test_reads_the_article_with_pause_speed_and_stop(self):
        self.tab.setHtml(ARTICLE, QUrl("https://tides.test/"))
        self.wait(lambda: self.tab.title() == "Tides")
        self.window.read_aloud()
        self.wait(lambda: self.reader.active and self.spoken)
        self.assertEqual(self.spoken[0], "Tides")
        self.assertTrue(self.reader.player.isVisible())
        self.reader.toggle()
        self.assertTrue(self.reader.paused)
        self.assertEqual(self.reader.player.toggle.text(), "Play")
        self.reader.toggle()
        self.reader.change_rate(0.3)
        self.assertEqual(self.window.config.get("read_aloud_rate"), 0.3)
        self.window.read_aloud()  # Again: stop.
        self.assertFalse(self.reader.active)
        self.assertFalse(self.reader.player.isVisible())


if __name__ == "__main__":
    unittest.main()
