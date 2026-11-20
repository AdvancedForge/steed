"""Skills: kept asks you run in one go, on the page you are on."""

import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import test_writing_help as fixture
from PyQt6.QtCore import QUrl

from services.skills import STARTERS, Skills, check


class Shop(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<title>Orders</title><p>Order 7 delivered</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class SkillStoreTests(unittest.TestCase):
    def test_starters_names_and_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            skills = Skills(folder)
            self.assertEqual(
                [s["name"] for s in skills.list()], [s["name"] for s in STARTERS]
            )
            again = Skills(folder)  # Starters only the first time.
            self.assertEqual(len(again.list()), len(STARTERS))
            added = skills.add({"name": "/Shopping list", "ask": "Make a list."})
            self.assertEqual(added["name"], "Shopping list")
            self.assertEqual(skills.find("  shopping   LIST")["id"], added["id"])
            with self.assertRaises(ValueError):
                skills.add({"name": "Shopping list", "ask": "Again"})
            with self.assertRaises(ValueError):
                check({"name": "x", "ask": "y", "context": "everything"})
            with self.assertRaises(ValueError):
                check({"name": "x", "ask": "y", "steps": [{"click": "Go"}]})
            skills.remove(added["id"])
            self.assertIsNone(skills.find("shopping list"))


class RunSkillTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WritingHelpTests.setUpClass.__func__)
    setUp = fixture.WritingHelpTests.setUp
    tearDown = fixture.WritingHelpTests.tearDown
    wait = fixture.WritingHelpTests.wait

    def test_a_skill_runs_on_this_page_and_its_steps(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Shop)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        loaded = []
        self.tab.loadFinished.connect(loaded.append)
        self.tab.setHtml(
            "<title>Mail</title><p>Lunch on Friday?</p>",
            QUrl("https://mail.example/inbox"),
        )
        self.wait(lambda: loaded)
        skills = self.window.profile_manager.registry.skills
        skill = skills.add(
            {
                "name": "Check orders",
                "ask": "Anything delivered?",
                "steps": [
                    {"open": f"http://127.0.0.1:{server.server_port}/orders"},
                    {"read": True},
                ],
            }
        )
        self.window._fill_skills_menu()
        labels = [a.text() for a in self.window.skills_menu.actions()]
        self.assertIn("Skill: Check orders", labels)
        self.assertIn("Manage Skills…", labels)
        with patch.object(fixture.BrowserWindow, "agent_message") as message:
            self.window.run_skill(skill["id"])
            self.wait(lambda: message.called)
        text = message.call_args.args[0]
        self.assertTrue(text.startswith("Skill “Check orders”: Anything delivered?"))
        self.assertIn(
            "[This page: Mail — https://mail.example/inbox]\nLunch on Friday?", text
        )
        self.assertIn("Order 7 delivered", text)  # What its steps read.
        self.assertEqual(message.call_args.kwargs, {"send": True})

    def test_the_editor_saves_and_writes_from_a_description(self):
        from ui.skills_dialog import SkillEditor

        class Once(fixture.FakeOnce):
            def __init__(self, command, prompt, parent=None, **_):
                fixture.QObject.__init__(self, parent)
                self.running = True
                fixture.QTimer.singleShot(
                    0, lambda: self.answered.emit("Turn the recipe into a list.")
                )

        editor = SkillEditor(self.window)
        editor.describe.setText("recipes into a shopping list")
        with (
            patch("services.agent_once.agent_command", return_value=["fake"]),
            patch("services.agent_once.AgentOnce", Once),
        ):
            editor.write_for_me()
            self.wait(lambda: editor.ask.toPlainText())
        self.assertEqual(editor.name.text(), "Recipes into a shopping list")
        editor.save()
        found = self.window.profile_manager.registry.skills.find(
            "recipes into a shopping list"
        )
        self.assertEqual(found["ask"], "Turn the recipe into a list.")


if __name__ == "__main__":
    unittest.main()
