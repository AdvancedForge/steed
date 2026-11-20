"""One picker for every assistant, grouped by where it runs, and the gallery
that adds them."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import test_task_services as fixtures
from qt_test_support import WAIT_SECONDS, application

from services.agent_providers import AgentProviders
from services.website_chats import clean_website_chat
from ui.add_assistant import AddAssistantDialog


class Config(dict):
    def set(self, key, value):
        self[key] = value


class Models(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"data": [{"id": "qwen3:8b"}, {"id": "llama3"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class PickerTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    setUp = fixtures.TaskServiceTests.setUp
    tearDown = fixtures.TaskServiceTests.tearDown

    def providers(self, config):
        registry = SimpleNamespace(
            tasks=self.service, config=config, set_agent_access=lambda _: None
        )
        manager = AgentProviders(registry)
        self.addCleanup(manager.close)
        return manager

    def test_assistants_are_grouped_and_only_what_is_set_up_shows(self):
        config = Config(
            models=[
                {
                    "name": "Local",
                    "base_url": "http://localhost:11434/v1",
                    "model": "qwen3:8b",
                },
                {
                    "name": "Hosted",
                    "base_url": "https://api.example.com/v1",
                    "model": "m",
                    "key": "env:X",
                },
            ],
            website_chats=[
                {"name": "ChatGPT", "url": "https://chatgpt.com/"},
                {"name": "Bad", "url": "http://insecure.example"},
            ],
        )
        manager = self.providers(config)
        with patch("shutil.which", return_value=None):
            found = manager.assistants()
        self.assertEqual(
            found,
            [
                ("model:Local", "Local", "On this computer"),
                ("model:Hosted", "Hosted", "With your API key"),
                ("web:ChatGPT", "ChatGPT", "Chatbot websites"),
            ],
        )
        with patch("shutil.which", return_value="/usr/bin/x"):
            keys = [key for key, *_ in manager.assistants()]
        self.assertIn("codex", keys)
        self.assertIn("claude", keys)

    def test_chat_websites_must_be_https(self):
        self.assertEqual(
            clean_website_chat({"name": " Claude ", "url": "https://claude.ai/"}),
            {"name": "Claude", "url": "https://claude.ai/"},
        )
        for bad in (
            {"name": "", "url": "https://a.test"},
            {"name": "X", "url": "http://a.test"},
            {"name": "X", "url": "https://user:pw@a.test"},
            "nope",
        ):
            with self.assertRaises(ValueError):
                clean_website_chat(bad)


class GalleryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_cards_say_what_they_are_and_report_the_choice(self):
        dialog = AddAssistantDialog(probe=False)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(
            list(dialog.cards), ["local", "key", "coding", "website", "cloud"]
        )
        chosen = []
        dialog.chosen.connect(lambda kind, detail: chosen.append((kind, detail)))
        dialog.cards["cloud"].click()
        self.assertEqual(chosen, [("cloud", None)])

    def test_a_model_server_already_running_is_found(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Models)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        address = f"http://127.0.0.1:{server.server_port}/v1"
        with patch("ui.add_assistant.LOCAL_SERVERS", (("Ollama", address),)):
            dialog = AddAssistantDialog()
        self.addCleanup(dialog.deleteLater)
        fixtures.TaskServiceTests.wait_for(self, lambda: dialog.found, WAIT_SECONDS)
        self.assertIn("Ollama (2 models)", dialog.detail("local"))
        chosen = []
        dialog.chosen.connect(lambda kind, detail: chosen.append((kind, detail)))
        dialog.cards["local"].click()
        self.assertEqual(chosen, [("local", address)])


if __name__ == "__main__":
    unittest.main()
