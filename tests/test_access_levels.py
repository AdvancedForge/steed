"""Access levels for agents and custom start pages, with presets and prompts."""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import test_task_profiles as fixtures
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from qt_test_support import WAIT_SECONDS, application, keep_directory

import access_levels
from browser_window import BrowserWindow
from services.agent_errors import AgentRequestError


class Weather(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"forecast": "sunny", "cookie": self.headers.get("Cookie")})
        kind = "application/json"
        if self.path.startswith("/site"):
            body, kind = "<title>site</title>A website", "text/html"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


class Config(dict):
    def set(self, key, value):
        self[key] = value


class AgentDecisionTests(unittest.TestCase):
    def test_presets_and_custom_policies_decide_requests(self):
        config = Config()
        decide = access_levels.agent_decision
        self.assertEqual(access_levels.preset_of(config, "agents"), "ask")
        self.assertEqual(decide(config, ["read"], named_profile=False), "ask")
        access_levels.apply_preset(config, "agents", "everything")
        self.assertEqual(
            decide(config, ["read", "submit"], named_profile=True), "approve"
        )
        access_levels.set_policy(config, "agents", "interact", "never")
        self.assertEqual(access_levels.preset_of(config, "agents"), "custom")
        self.assertEqual(decide(config, ["read"], named_profile=False), "approve")
        self.assertEqual(decide(config, ["read", "edit"], named_profile=False), "deny")
        access_levels.set_policy(config, "agents", "interact", "ask")
        self.assertEqual(decide(config, ["read", "edit"], named_profile=False), "ask")
        access_levels.apply_preset(config, "agents", "nothing")
        self.assertEqual(decide(config, ["read"], named_profile=False), "deny")
        self.assertEqual(
            access_levels.blocked_capabilities(config, ["read"], named_profile=True),
            ["Read and browse pages", "Your signed-in profiles"],
        )
        for bad in (("agents", "browse", "maybe"), ("agents", "teleport", "ask")):
            with self.assertRaises(ValueError):
                access_levels.set_policy(config, *bad)
        config["agent_access"] = {"browse": "sometimes"}
        self.assertEqual(access_levels.values(config, "agents")["browse"], "ask")
        # Local pages start isolated.
        self.assertEqual(access_levels.preset_of(Config(), "pages"), "nothing")


class AgentGrantTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskProfileTests.tearDownClass.__func__)
    setUp = fixtures.TaskProfileTests.setUp
    wait_for = fixtures.TaskProfileTests.wait_for
    command = fixtures.TaskProfileTests.command
    request = fixtures.TaskProfileTests.request

    def tearDown(self):
        access_levels.apply_preset(self.registry.config, "agents", "ask")
        fixtures.TaskProfileTests.tearDown(self)

    def ask_for(self, actions):
        task = self.command("task.create", objective="Access settings")
        return task.task_id, self.request(task.task_id, actions=actions)

    def test_never_refuses_and_everything_approves_without_asking(self):
        access_levels.apply_preset(self.registry.config, "agents", "nothing")
        with self.assertRaises(AgentRequestError) as refused:
            self.ask_for(["read", "navigate"])
        self.assertEqual(refused.exception.code, "access_blocked")
        self.assertIn("Read and browse pages", refused.exception.public_message)
        access_levels.apply_preset(self.registry.config, "agents", "everything")
        task, grant = self.ask_for(["read", "navigate"])
        self.assertEqual(grant.status, "approved")
        events = [e.kind for e in self.service.store.events(task)]
        self.assertIn("permission.auto_approved", events)
        access_levels.apply_preset(self.registry.config, "agents", "ask")
        _, grant = self.ask_for(["read", "navigate"])
        self.assertEqual(grant.status, "requested")

    def test_one_never_capability_blocks_only_requests_that_need_it(self):
        access_levels.apply_preset(self.registry.config, "agents", "everything")
        access_levels.set_policy(self.registry.config, "agents", "files", "never")
        _, grant = self.ask_for(["read", "navigate"])
        self.assertEqual(grant.status, "approved")
        with self.assertRaises(AgentRequestError):
            self.ask_for(["read", "navigate", "download"])


PAGE = """<title>Dashboard</title><script>
window.results = {};
for (const name of ['tabs', 'bookmarks', 'history']) {
  fetch('steed://newtab/api/' + name).then(r => r.json())
    .then(data => { window.results[name] = data; })
    .catch(e => { window.results[name] = 'failed: ' + e; });
}
fetch('steed://newtab/api/fetch?url=' + encodeURIComponent(WEATHER)).then(r => r.json())
  .then(data => { window.results.web = data; })
  .catch(e => { window.results.web = 'failed: ' + e; });
</script>"""


class StartPageApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Weather)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.weather = f"http://127.0.0.1:{cls.server.server_port}/forecast"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home_patch = patch(
            "pathlib.Path.home", return_value=Path(self.directory.name)
        )
        self.home_patch.start()
        self.window = BrowserWindow()
        self.window.show()
        self.registry = self.window.profile_manager.registry
        self.profile = self.window.profile_manager.current_profile_name
        pages = self.registry.start_pages
        pages.add_bookmark(self.profile, "https://example.org/", "Example")
        pages.configure(
            self.profile,
            pages.state(self.profile)["settings"],
            PAGE.replace("WEATHER", json.dumps(self.weather)),
        )

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def results(self, tab):
        out = []
        tab.page().runJavaScript("JSON.stringify(window.results || {})", out.append)
        self.wait(lambda: bool(out))
        return json.loads(out[0])

    def open_dashboard(self):
        tab = self.window._new_tab(url="steed:newtab", return_tab=True)
        self.wait(lambda: tab.title() == "Dashboard")
        self.wait(lambda: len(self.results(tab)) == 4)
        return self.results(tab)

    def test_isolated_by_default(self):
        results = self.open_dashboard()
        for name in ("tabs", "bookmarks", "history", "web"):
            self.assertEqual(results[name]["error"], "denied")

    def test_everything_serves_browser_data_and_cookieless_web_fetches(self):
        access_levels.apply_preset(self.registry.config, "pages", "everything")
        results = self.open_dashboard()
        self.assertIn(
            {"title": "Example", "url": "https://example.org/"}, results["bookmarks"]
        )
        self.assertTrue(any("newtab" in t["url"] for t in results["tabs"]))
        self.assertIsInstance(results["history"], list)
        self.assertEqual(results["web"], {"forecast": "sunny", "cookie": None})

    def test_ask_prompts_once_per_capability_and_remembers_the_session(self):
        from PyQt6.QtWidgets import QMessageBox

        access_levels.apply_preset(self.registry.config, "pages", "ask")
        asked = []

        def answer(box):
            asked.append(box.text())
            label = "Allow this session" if "web" in box.text() else "Don't allow"
            button = next(b for b in box.buttons() if b.text() == label)
            button.click()

        with patch.object(QMessageBox, "show", lambda box: answer(box)):
            results = self.open_dashboard()
        self.assertEqual(len(asked), 4)
        self.assertEqual(results["web"]["forecast"], "sunny")
        self.assertEqual(results["tabs"]["error"], "denied")
        # The session decision is remembered: no new questions.
        with patch.object(QMessageBox, "show", lambda box: self.fail("asked again")):
            results = self.open_dashboard()
        self.assertEqual(results["web"]["forecast"], "sunny")

    def test_only_the_start_page_itself_can_call_the_api(self):
        access_levels.apply_preset(self.registry.config, "pages", "everything")
        site = self.weather.replace("/forecast", "/site")
        tab = self.window._new_tab(url=site, return_tab=True)
        self.wait(lambda: tab.title() == "site")
        tab.page().runJavaScript(
            "fetch('steed://newtab/api/bookmarks').then(r => r.text())"
            ".then(t => window.leak = 'got ' + t).catch(e => window.leak = 'blocked')"
        )
        seen = []

        def leaked():
            tab.page().runJavaScript("window.leak || ''", seen.append)
            return bool(seen and seen[-1])

        self.wait(leaked)
        self.assertEqual(seen[-1], "blocked")
        self.assertNotEqual(QUrl(self.weather).scheme(), "steed")


class AccessPageTests(unittest.TestCase):
    setUpClass = classmethod(StartPageApiTests.setUpClass.__func__)
    tearDownClass = classmethod(StartPageApiTests.tearDownClass.__func__)
    setUp = StartPageApiTests.setUp
    tearDown = StartPageApiTests.tearDown
    wait = StartPageApiTests.wait

    def test_page_applies_presets_and_single_capabilities(self):
        tab = self.window._new_tab(url="steed:access", return_tab=True)
        page = tab.workspace_controller
        self.wait(lambda: page.surface.ready)

        def send(**command):
            page.command(
                {"context": page.context, "sequence": page.sequence + 1, **command}
            )

        send(action="preset", area="agents", preset="nothing")
        self.assertEqual(
            access_levels.preset_of(self.registry.config, "agents"), "nothing"
        )
        send(action="set", area="pages", capability="web", policy="always")
        self.assertEqual(
            access_levels.values(self.registry.config, "pages")["web"], "always"
        )
        self.assertEqual(
            access_levels.preset_of(self.registry.config, "pages"), "custom"
        )
        send(action="set", area="pages", capability="web", policy="sometimes")
        self.assertIn("Never, Ask or Always", page.error)
        send(action="preset", area="robots", preset="everything")
        self.assertTrue(page.error)
        page.command(
            {
                "context": "forged",
                "sequence": 99,
                "action": "preset",
                "area": "agents",
                "preset": "everything",
            }
        )
        self.assertEqual(
            access_levels.preset_of(self.registry.config, "agents"), "nothing"
        )


if __name__ == "__main__":
    unittest.main()
