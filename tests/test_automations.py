"""Automations: a trigger and steps, with or without AI."""

import json
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import test_writing_help as fixture
from PyQt6.QtCore import QObject, QTimer, QUrl

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

    def test_new_triggers_start_them_with_what_happened(self):
        from types import SimpleNamespace

        service = self.automations()
        profile = self.window.profile_manager.current_profile_name
        service.add(
            {
                "name": "PDFs",
                "trigger": {"kind": "download", "match": ".pdf"},
                "steps": [{"do": "notify", "value": "{last}"}],
            },
            profile,
        )
        service.add(
            {
                "name": "Shop",
                "trigger": {"kind": "visit", "site": "https://www.Shop.example/x"},
                "steps": [{"do": "notify", "value": "{last}"}],
            },
            profile,
        )
        record = SimpleNamespace(
            name="bill.pdf",
            path="/d/bill.pdf",
            url="https://a.example/",
            profile=profile,
        )
        service.download_finished(SimpleNamespace(**{**vars(record), "name": "a.zip"}))
        service.download_finished(
            SimpleNamespace(**{**vars(record), "profile": "search"})
        )
        service.download_finished(record)
        self.wait(lambda: self.notes)
        self.assertEqual(
            self.notes, [("PDFs", "bill.pdf\n/d/bill.pdf\nfrom https://a.example/")]
        )
        service.visited(profile, "https://news.example/", "News")
        service.visited("search", "https://www.shop.example/", "Shop")
        service.visited(profile, "https://cart.www.shop.example/basket", "Basket")
        service.visited(profile, "https://www.shop.example/again", "Again")  # Too soon.
        self.wait(lambda: len(self.notes) == 2)
        time.sleep(0.1)
        self.app.processEvents()
        self.assertEqual(
            self.notes[1],
            ("Shop", "### Basket\nhttps://cart.www.shop.example/basket"),
        )
        self.assertEqual(len(self.notes), 2)

    def test_shortcut_and_page_menu_triggers(self):
        from PyQt6.QtWidgets import QMenu

        service = self.automations()
        profile = self.window.profile_manager.current_profile_name
        keyed = service.add(
            {
                "name": "Keyed",
                "trigger": {"kind": "shortcut", "keys": "ctrl+alt+k"},
                "steps": [{"do": "notify", "value": "pressed"}],
            },
            profile,
        )
        self.assertEqual(keyed["trigger"]["keys"], "Ctrl+Alt+K")
        for bad in ("k", "", "Ctrl+K, Ctrl+L"):
            with self.assertRaises(ValueError, msg=bad):
                check(
                    {
                        "name": "x",
                        "trigger": {"kind": "shortcut", "keys": bad},
                        "steps": [{"do": "changed"}],
                    }
                )
        self.window._fill_automations_menu()
        (action,) = [
            a
            for a in self.window.automations_menu.actions()
            if a.text().endswith("Keyed")
        ]
        self.assertEqual(action.shortcut().toString(), "Ctrl+Alt+K")
        self.assertIn(action, self.window.actions())
        service.add(
            {
                "name": "Summarize here",
                "trigger": {"kind": "page"},
                "steps": [{"do": "notify", "value": "{last}"}],
            },
            profile,
        )
        tab = self.window.tabs[self.window.current_tab_index]
        tab.setHtml(
            "<title>Deal</title><p>Half price kettles</p>",
            QUrl("https://deal.example/"),
        )
        self.wait(
            lambda: (
                tab.title() == "Deal"
                and tab.url().toString() == "https://deal.example/"
                and not tab.page().isLoading()
            )
        )
        menu = QMenu()
        self.window._add_ask_agent_item(menu, tab, None)
        (runs,) = [a.menu() for a in menu.actions() if a.menu()]
        self.assertEqual(runs.title(), "Run Automation on This Page")
        runs.actions()[0].trigger()
        self.wait(lambda: self.notes)
        self.assertEqual(self.notes[0][0], "Summarize here")
        self.assertIn("https://deal.example/", self.notes[0][1])
        self.assertIn("Half price kettles", self.notes[0][1])

    def test_decide_save_and_wait_steps(self):
        service = self.automations()
        registry = self.window.profile_manager.registry
        item = service.add(
            {
                "name": "Deal",
                "steps": [
                    {"do": "decide", "value": "Is this cheap? {last}"},
                    {"do": "save", "value": "deal {date}.md"},
                    {"do": "wait", "value": "1"},
                    {"do": "notify", "value": "{last}"},
                ],
            }
        )
        for bad in (
            {"do": "wait", "value": "0"},
            {"do": "wait", "value": "x"},
            {"do": "save", "value": "../escape"},
            {"do": "save", "value": ""},
        ):
            with self.assertRaises(ValueError, msg=bad):
                check({"name": "x", "steps": [bad]})

        class Says(fixture.FakeOnce):
            answer = "No."

            def __init__(self, command, prompt, parent=None, **_):
                QObject.__init__(self, parent)
                fixture.FakeOnce.asked.append(prompt)
                QTimer.singleShot(0, lambda: self.answered.emit(Says.answer))

        timers = []
        real_start = QTimer.start

        def quick(timer, ms=None):
            if ms == 60_000:
                timers.append(ms)
                ms = 10
            return real_start(timer) if ms is None else real_start(timer, ms)

        with (
            patch("services.agent_once.agent_command", return_value=["fake"]),
            patch("services.agent_once.AgentOnce", Says),
            patch.object(QTimer, "start", quick),
        ):
            service.run(item["id"], start="Kettle, 12 pounds")
            self.wait(lambda: self.outcomes)
            self.assertEqual(
                self.outcomes[-1], "Stopped at step 1: your assistant said no."
            )
            self.assertIn(
                "Answer only yes or no. Is this cheap? Kettle",
                fixture.FakeOnce.asked[-1],
            )
            Says.answer = "Yes, it is."
            service.run(item["id"], start="Kettle, 12 pounds")
            self.wait(lambda: len(self.outcomes) == 2)
        self.assertEqual(self.outcomes[-1], "Done (4 steps).")
        self.assertEqual(timers, [60_000])
        self.assertEqual(self.notes, [("Deal", "Kettle, 12 pounds")])
        library = registry.tasks.library
        (folder,) = [f for f in library.folders() if f["name"] == "Automations"]
        self.assertFalse(folder["enabled"])  # Not shared with agents by itself.
        (entry,) = library.list(folder["id"])["entries"]
        self.assertTrue(entry["path"].startswith("deal 20"))

    def test_the_editor_sets_the_new_triggers(self):
        from ui.automations_dialog import AutomationEditor

        editor = AutomationEditor(self.window)
        self.addCleanup(editor.deleteLater)
        editor.name.setText("Visit")
        editor.trigger.setCurrentIndex(editor.trigger.findData("visit"))
        self.assertFalse(editor.trigger_value.isHidden())
        editor.trigger_value.setText("shop.example")
        editor.step_kind.setCurrentIndex(editor.step_kind.findData("wait"))
        editor.step_value.setText("5")
        editor.add_step()
        self.assertEqual(
            editor.values()["trigger"], {"kind": "visit", "site": "shop.example"}
        )
        self.assertEqual(editor.steps, [{"do": "wait", "value": "5"}])
        editor.load(
            check(
                {
                    "name": "K",
                    "trigger": {"kind": "shortcut", "keys": "Alt+J"},
                    "steps": [{"do": "changed"}],
                }
            )
        )
        self.assertEqual(editor.trigger_value.text(), "Alt+J")
        editor.trigger.setCurrentIndex(editor.trigger.findData("page"))
        self.assertTrue(editor.trigger_value.isHidden())

    def test_each_run_keeps_what_every_step_had_and_gave(self):
        from services.automations import RUNS_KEPT
        from ui.automations_dialog import AutomationsDialog, describe_run

        site = self.serve()
        service = self.automations()
        item = service.add(
            {
                "name": "Kettle",
                "steps": [
                    {"do": "read", "value": site + "/kettle"},
                    {"do": "contains", "value": "sold out"},
                    {"do": "notify", "value": "{last}"},
                ],
            }
        )
        Stock.level = "in stock"
        service.run(item["id"], start="pressed Run now")
        self.wait(lambda: self.outcomes)
        Stock.level = "sold out"
        service.run(item["id"])
        self.wait(lambda: len(self.outcomes) == 2)
        latest, first = service.history(item["id"])
        self.assertEqual(latest["outcome"], "Done (3 steps).")
        self.assertEqual([s["n"] for s in latest["steps"]], [1, 2, 3])
        self.assertIn("sold out", latest["steps"][0]["gave"])
        self.assertEqual(first["start"], "pressed Run now")
        stopped = first["steps"][-1]
        self.assertEqual(stopped["n"], 2)
        self.assertTrue(stopped["stopped"])
        self.assertNotIn("gave", stopped)
        text = describe_run(first)
        self.assertIn("2. Go on only if it contains “sold out”", text)
        self.assertIn("stopped here", text)
        # Shown from Automations → History…, newest first.
        dialog = AutomationsDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        dialog.table.setCurrentCell(0, 0)
        history = dialog.history()
        self.assertEqual(history.runs.count(), 2)
        self.assertIn("Done (3 steps).", history.runs.item(0).text())
        self.assertIn("1. Read", history.detail.toPlainText())
        # Only the last RUNS_KEPT; kept on disk; cleared on request or removal.
        service.runs[item["id"]] = [dict(first, outcome=str(n)) for n in range(40)]
        service._keep_run(
            service.get(item["id"]), {"started": 1, "start": "", "log": []}, "new"
        )
        self.assertEqual(len(service.history(item["id"])), RUNS_KEPT)
        self.assertEqual(service.history(item["id"])[0]["outcome"], "new")
        self.assertTrue(service.runs_path.exists())
        history.clear()
        self.assertEqual(service.history(item["id"]), [])
        self.assertEqual(history.runs.count(), 0)
        service.run(item["id"])
        self.wait(lambda: len(self.outcomes) == 3)
        service.remove(item["id"])
        self.assertNotIn(item["id"], service.runs)

    def test_examples_open_in_the_editor_and_keep_their_days(self):
        from services.automations import starters
        from ui.automations_dialog import AutomationsDialog

        self.assertGreaterEqual(len(starters()), 5)
        for title, what, automation, hint in starters():
            check(automation)  # Every example is a valid automation.
            self.assertTrue(what and hint, title)
        service = self.automations()
        dialog = AutomationsDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        examples = dialog.examples()
        self.assertEqual(examples.list.count(), len(starters()))
        examples.list.setCurrentRow(0)
        editor = examples.use()
        self.assertEqual(editor.name.text(), "Morning brief")
        self.assertIn("calendar", editor.status.text())
        self.assertEqual(editor.days.currentText(), "Weekdays")
        editor.save()
        (saved,) = service.list()
        self.assertEqual(saved["trigger"]["days"], [0, 1, 2, 3, 4])  # Not every day.
        # An unusual set of days is kept, and can be changed.
        from ui.automations_dialog import AutomationEditor

        editor = AutomationEditor(
            self.window, dict(saved, trigger=dict(saved["trigger"], days=[1, 3]))
        )
        self.addCleanup(editor.deleteLater)
        self.assertEqual(editor.days.currentText(), "Tue, Thu")
        editor.days.setCurrentIndex(editor.days.findText("Weekends"))
        self.assertEqual(editor.values()["trigger"]["days"], [5, 6])


if __name__ == "__main__":
    unittest.main()
