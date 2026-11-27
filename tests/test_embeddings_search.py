"""Find a Page I Saw by meaning, with your own embeddings service."""

import json
import re
import tempfile
import threading
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_writing_help as fixture
from qt_test_support import application

from services import embeddings
from services.embeddings import Embedder, VectorCache, cosine_rank

DIMENSIONS = 64


def vector(text):
    """Bag of words: texts sharing words point the same way."""
    found = [0.0] * DIMENSIONS
    for word in re.findall(r"[a-z]+", text.lower()):
        found[zlib.crc32(word.rstrip("s").encode()) % DIMENSIONS] += 1.0
    return found


class Service(BaseHTTPRequestHandler):
    seen = []
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Service.seen.append((self.path, dict(self.headers), body))
        rows = [
            {"object": "embedding", "index": i, "embedding": vector(text)}
            for i, text in enumerate(body["input"])
        ]
        reply = json.dumps({"data": rows[::-1]}).encode()  # Any order.
        self.send_response(Service.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


def start_service(test):
    Service.seen.clear()
    Service.status = 200
    server = HTTPServer(("127.0.0.1", 0), Service)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return f"http://127.0.0.1:{server.server_port}/v1"


class Config(dict):
    def __init__(self, directory, **values):
        super().__init__(values)
        self.config_dir = Path(directory)

    def set(self, key, value):
        self[key] = value


class RankingTests(unittest.TestCase):
    def test_closest_first_and_mismatched_vectors_skipped(self):
        vectors = [[0, 1], [1, 0.1], None, [1, 0, 0], [0.7, 0.7]]
        self.assertEqual(cosine_rank([1, 0], vectors), [1, 4, 0])
        self.assertEqual(cosine_rank([1, 0], vectors, limit=1), [1])

    def test_cache_keeps_vectors_by_model_and_text_and_prunes(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = VectorCache(Path(directory) / "e.sqlite")
            cache.put("m", ["a", "b"], [[1.0, 2.0], [3.0, 4.0]])
            self.assertEqual(cache.get("m", ["b", "c", "a"]), [[3, 4], None, [1, 2]])
            self.assertEqual(cache.get("other", ["a"]), [None])
            with patch.object(embeddings, "MAX_VECTORS", 2):
                cache.put("m", ["c"], [[5.0, 6.0]])
            self.assertEqual(
                cache.db.execute("SELECT count(*) FROM vectors").fetchone()[0], 2
            )
            cache.close()

    def test_service_addresses_are_checked(self):
        self.assertEqual(
            embeddings.check_service("http://localhost:11434/v1/"),
            "http://localhost:11434/v1",
        )
        with self.assertRaises(ValueError):
            embeddings.check_service("http://example.com/v1")


class EmbedderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.url = start_service(self)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Config(
            directory.name, embeddings_service=self.url, embeddings_model="tiny"
        )
        self.result = SimpleNamespace(done=[], failed=[])

    def embed(self, texts, key=""):
        embedder = Embedder(self.config, key=key)
        embedder.done.connect(self.result.done.append)
        embedder.failed.connect(self.result.failed.append)
        embedder.embed(texts)
        fixture.WritingHelpTests.wait(
            SimpleNamespace(app=self.app, fail=self.fail),
            lambda: self.result.done or self.result.failed,
        )
        return embedder

    def test_texts_go_with_the_model_and_come_back_in_order(self):
        self.embed(["heat pumps", "bread recipe"], key="sk-test")
        self.assertEqual(
            self.result.done[0], [vector("heat pumps"), vector("bread recipe")]
        )
        path, headers, body = Service.seen[0]
        self.assertEqual(path, "/v1/embeddings")
        self.assertEqual(
            body, {"model": "tiny", "input": ["heat pumps", "bread recipe"]}
        )
        self.assertEqual(headers["Authorization"], "Bearer sk-test")

    def test_each_text_is_embedded_once(self):
        self.embed(["heat pumps", "bread recipe"])
        self.assertNotIn("Authorization", Service.seen[0][1])
        self.result.done.clear()
        self.embed(["bread recipe", "garden tomatoes"])
        self.assertEqual(len(Service.seen), 2)
        self.assertEqual(Service.seen[1][2]["input"], ["garden tomatoes"])
        self.assertEqual(
            self.result.done[0], [vector("bread recipe"), vector("garden tomatoes")]
        )

    def test_large_lists_go_in_batches(self):
        texts = [f"page {n} words" for n in range(embeddings.BATCH + 3)]
        self.embed(texts)
        self.assertEqual([len(s[2]["input"]) for s in Service.seen], [32, 3])
        self.assertEqual(len(self.result.done[0]), len(texts))

    def test_failures_are_reported(self):
        Service.status = 401
        self.embed(["x"], key="wrong")
        self.assertEqual(
            self.result.failed, ["The embeddings service refused the key."]
        )
        self.result.failed.clear()
        Service.status = 500
        self.embed(["y"])
        self.assertIn("did not answer", self.result.failed[0])

    def test_not_set_up(self):
        self.config["embeddings_model"] = ""
        self.embed(["x"])
        self.assertEqual(self.result.failed, ["Set up your embeddings service first."])
        self.assertEqual(Service.seen, [])


class FindByMeaningTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WritingHelpTests.setUpClass.__func__)
    setUp = fixture.WritingHelpTests.setUp
    tearDown = fixture.WritingHelpTests.tearDown
    wait = fixture.WritingHelpTests.wait

    def test_no_assistant_needed_and_the_closest_page_comes_first(self):
        url = start_service(self)
        history = self.window.history
        history.record("https://kitchen.example/bread", "Sourdough bread recipe")
        history.record("https://energy.example/heat", "Heat pumps for old houses")
        history.record("https://garden.example/", "Growing tomatoes")
        self.window.config.set("find_page_with", "embeddings")
        shown = []
        with (
            patch("ui.assistant_choice.ensure", side_effect=AssertionError),
            patch.object(
                fixture.BrowserWindow,
                "_present_dialog",
                lambda self, dialog, **_: shown.append(dialog),
            ),
            patch("services.embeddings.api_key", return_value=""),
        ):
            self.window.find_in_history()
            dialog = shown[0]
            self.assertFalse(dialog.setup.isHidden())
            dialog.service.setText(url)
            dialog.model.setText("tiny")
            dialog.save_service()
            self.assertEqual(dialog.status.text(), "Saved.")
            self.assertEqual(self.window.config.get("embeddings_service"), url)
            dialog.description.setText("that article about heat pumps")
            dialog.search()
            self.wait(lambda: dialog.go.isEnabled() and dialog.matches)
        self.assertEqual(dialog.matches[0][1], "https://energy.example/heat")
        sent = Service.seen[0][2]["input"]
        self.assertEqual(sent[0], "that article about heat pumps")
        self.assertIn("Heat pumps for old houses — energy.example", sent)

    def test_choosing_the_assistant_hides_the_service_fields(self):
        from ui.history_find import FindInHistoryDialog

        dialog = FindInHistoryDialog(self.window, ["fake"])
        dialog.find_with.setCurrentIndex(1)
        self.assertEqual(self.window.config.get("find_page_with"), "embeddings")
        self.assertFalse(dialog.setup.isHidden())
        dialog.find_with.setCurrentIndex(0)
        self.assertTrue(dialog.setup.isHidden())
        self.assertIn("looks through the titles", dialog.note.text())
        dialog.close()


if __name__ == "__main__":
    unittest.main()
