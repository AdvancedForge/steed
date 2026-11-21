"""Automations: a trigger and steps, with or without AI."""

import json
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import test_writing_help as fixture

from services.automations import (
    check,
    describe_step,
    describe_trigger,
    parse_written,
)


class Stock(BaseHTTPRequestHandler):
    level = "in stock"

    def do_GET(self):
        body = f"<title>Kettle</title><p>Steel kettle: {Stock.level}</p>".encode()
        if self.path == "/feed.json":
            body = json.dumps({"price": 40}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class CheckTests(unittest.TestCase):
    def test_triggers_and_steps_are_checked_and_described(self):
        automation = check(
            {
                "name": "  Morning   rates ",
                "trigger": {"kind": "schedule", "at": "07:30", "days": [0, 1, 2, 3, 4]},
                "steps": [
                    {"do": "read", "value": "https://bank.example/rates"},
                    {"do": "changed"},
                    {"do": "notify", "value": "{last}"},
                ],
            }
        )
        self.assertEqual(automation["name"], "Morning rates")
        self.assertEqual(describe_trigger(automation["trigger"]), "Weekdays at 07:30")
        self.assertEqual(automation["steps"][2]["title"], "Morning rates")
        self.assertEqual(
            [describe_step(s) for s in automation["steps"]],
            [
                "Read https://bank.example/rates",
                "Go on only if it changed since the last run",
                "Notify: {last}",
            ],
        )
        hourly = check(
            {
                "name": "x",
                "trigger": {"kind": "schedule", "every": 4},
                "steps": [{"do": "changed"}],
            }
        )
        self.assertEqual(describe_trigger(hourly["trigger"]), "Every 4 hours")
        for bad in (
            {"name": "", "steps": [{"do": "changed"}]},
            {"name": "x", "steps": []},
            {"name": "x", "steps": [{"do": "shell", "value": "rm -rf ~"}]},
            {"name": "x", "steps": [{"do": "read", "value": "file:///etc/passwd"}]},
            {"name": "x", "steps": [{"do": "ask"}]},
            {"name": "x", "trigger": {"kind": "watch"}, "steps": [{"do": "changed"}]},
            {"name": "x", "trigger": {"kind": "hourly"}, "steps": [{"do": "changed"}]},
        ):
            with self.assertRaises(ValueError, msg=bad):
                check(bad)

    def test_what_an_assistant_writes_is_checked_like_anything_else(self):
        written = parse_written(
            'Here you go:\n{"name": "Prices", "trigger": {"kind": "manual"}, '
            '"steps": [{"do": "fetch", "value": "https://shop.example/p.json"}, '
            '{"do": "notify", "value": "{last}"}]}'
        )
        self.assertEqual(written["steps"][0]["do"], "fetch")
        for bad in ("no json", '{"name": "x", "steps": [{"do": "shell"}]}'):
            with self.assertRaises(ValueError):
                parse_written(bad)


class RunTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WritingHelpTests.setUpClass.__func__)
    setUp = fixture.WritingHelpTests.setUp
    tearDown = fixture.WritingHelpTests.tearDown
    wait = fixture.WritingHelpTests.wait

    def serve(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Stock)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def automations(self):
        service = self.window.profile_manager.registry.automations
        self.outcomes, self.notes, self.chats = [], [], []
        service.finished.connect(lambda a, outcome: self.outcomes.append(outcome))
        service.notify.connect(lambda title, text: self.notes.append((title, text)))
        service.chat.connect(lambda a, text: self.chats.append(text))
        return service

    def test_read_a_page_go_on_only_when_it_changed_and_notify(self):
        site = self.serve()
        service = self.automations()
        item = service.add(
            {
                "name": "Kettle",
                "steps": [
                    {"do": "read", "value": site + "/kettle"},
                    {"do": "contains", "value": "kettle"},
                    {"do": "changed"},
                    {"do": "notify", "value": "Now: {last}", "title": "Kettle changed"},
                ],
            }
        )
        Stock.level = "in stock"
        service.run(item["id"])
        self.wait(lambda: self.outcomes)
        self.assertEqual(self.outcomes[-1], "Stopped at step 3: first run, remembered.")
        Stock.level = "sold out"
        service.run(item["id"])
        self.wait(lambda: len(self.outcomes) == 2)
        self.assertEqual(self.outcomes[-1], "Done (4 steps).")
        self.assertEqual(
            self.notes, [("Kettle changed", "Now: Steel kettle: sold out")]
        )
        self.assertTrue(service.get(item["id"])["last_run"])

    def test_fetch_ask_your_assistant_then_hand_it_to_an_agent(self):
        site = self.serve()
        service = self.automations()
        item = service.add(
            {
                "name": "Price",
                "steps": [
                    {"do": "fetch", "value": site + "/feed.json"},
                    {"do": "ask", "value": "What is the price?"},
                    {"do": "chat", "value": "Buy it if {last}"},
                ],
            }
        )
        with (
            patch("services.agent_once.agent_command", return_value=["fake"]),
            patch("services.agent_once.AgentOnce", fixture.FakeOnce),
        ):
            service.run(item["id"])
            self.wait(lambda: self.outcomes)
        self.assertIn('{"price": 40}', fixture.FakeOnce.asked[-1])
        self.assertEqual(
            self.chats,
            ["Automation “Price”: Buy it if Hello world, see you tomorrow."],
        )
        # Paused agents: steps that use them stop the run.
        tasks = self.window.profile_manager.registry.tasks
        tasks.set_agents_paused(True)
        service.run(item["id"])
        self.wait(lambda: len(self.outcomes) == 2)
        tasks.set_agents_paused(False)
        self.assertEqual(self.outcomes[-1], "Stopped at step 2: agents are paused.")

    def test_schedules_watches_and_startup_start_them(self):
        service = self.automations()
        daily = service.add(
            {
                "name": "Daily",
                "trigger": {"kind": "schedule", "at": "00:00"},
                "steps": [{"do": "notify", "value": "hi"}],
            }
        )
        watched = service.add(
            {
                "name": "On change",
                "trigger": {"kind": "watch", "watch": "w1"},
                "steps": [{"do": "notify", "value": "{last}"}],
            }
        )
        due = service.due(datetime.now().replace(hour=23, minute=59))
        self.assertIn(daily["id"], [a["id"] for a in due])
        self.assertNotIn(watched["id"], [a["id"] for a in due])
        service.watch_changed({"id": "w1", "title": "Rent"}, "due Friday")
        self.wait(lambda: self.notes)
        self.assertEqual(self.notes[-1], ("On change", "Rent: due Friday"))
        service.watch_changed({"id": "other", "title": "x"}, "y")
        time.sleep(0.2)
        self.app.processEvents()
        self.assertEqual(len(self.notes), 1)

    def test_routines_become_automations_once(self):
        from browser.utils import save_json
        from services.routines import Routines

        registry = self.window.profile_manager.registry
        path = registry.config.config_dir / "routines.json"
        save_json(
            path,
            [
                {
                    "id": "r1",
                    "name": "Mail",
                    "ask": "Anything new?",
                    "kind": "daily",
                    "at": "07:00",
                    "days": [0, 1, 2],
                    "pages": ["https://mail.example/"],
                    "signin": True,
                    "brief": True,
                    "profile": "global",
                },
                {
                    "id": "r2",
                    "name": "News",
                    "ask": "x",
                    "kind": "hours",
                    "every": 6,
                    "pages": ["https://news.example/"],
                    "tell": "changes",
                },
            ],
        )
        routines = Routines(registry)
        self.assertEqual(routines.list(), [])
        routines.close()
        self.assertFalse(path.exists())
        self.assertTrue(path.with_name("routines.migrated.json").exists())
        mail = registry.automations.get("r1")
        self.assertEqual(describe_trigger(mail["trigger"]), "Mon, Tue, Wed at 07:00")
        self.assertEqual(
            [s["do"] for s in mail["steps"]], ["gather", "noticed", "chat"]
        )
        self.assertTrue(mail["steps"][0]["signin"])
        news = registry.automations.get("r2")
        self.assertEqual(describe_trigger(news["trigger"]), "Every 6 hours")
        self.assertEqual(
            [s["do"] for s in news["steps"]], ["gather", "changed", "notify"]
        )

    def test_the_editor_builds_steps_and_saves(self):
        from ui.automations_dialog import AutomationEditor, AutomationsDialog

        editor = AutomationEditor(self.window)
        editor.name.setText("Look")
        editor.step_kind.setCurrentIndex(editor.step_kind.findData("open"))
        editor.step_value.setText("not a url")
        editor.add_step()
        self.assertIn("http", editor.status.text())
        editor.step_value.setText("https://example.com/")
        editor.add_step()
        editor.step_kind.setCurrentIndex(editor.step_kind.findData("changed"))
        editor.add_step()
        editor.list.setCurrentRow(1)
        editor.move_step(-1)
        self.assertEqual([s["do"] for s in editor.steps], ["changed", "open"])
        editor.save()
        service = self.window.profile_manager.registry.automations
        self.assertEqual(service.list()[0]["name"], "Look")
        dialog = AutomationsDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 1).text(), "When you run it")
        self.window._fill_automations_menu()
        labels = [a.text() for a in self.window.automations_menu.actions()]
        self.assertIn("Run Automation: Look", labels)


if __name__ == "__main__":
    unittest.main()
