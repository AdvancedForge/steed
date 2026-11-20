"""Chats with a model Steed runs itself, through Steed's own browser tools."""

import json
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import test_task_services as fixtures

import services.model_assistant as model_assistant
from services.agent_gateway import AgentGateway
from services.agent_providers import AgentProviders
from services.model_assistant import BROWSING_TOOLS, clean_model, tool_command
from ui.agent_sidebar import AgentSidebar

KEY = "synthetic-test-key"


class FakeChatApi(BaseHTTPRequestHandler):
    """A scripted OpenAI-compatible /chat/completions endpoint."""

    requests = []
    status = 200

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": body,
            }
        )
        if type(self).status != 200:
            self._send(type(self).status, {"error": {"message": "nope"}})
            return
        last = body["messages"][-1]
        if last["role"] == "tool" and last["content"].startswith("Error"):
            message = {"role": "assistant", "content": "Refused: " + last["content"]}
        elif last["role"] == "tool":
            result = json.loads(last["content"])
            message = {
                "role": "assistant",
                "content": "Your browser: "
                + ("ok" if "result" in result else result["error"]["code"]),
            }
        elif last["content"] == "use a tool":
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "steed_task_capabilities",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        elif last["content"] == "read my tabs":
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-3",
                        "type": "function",
                        "function": {
                            "name": "steed_browser_context",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        elif last["content"] == "made-up tool":
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {"name": "rm_rf", "arguments": "{}"},
                    }
                ],
            }
        else:
            message = {"role": "assistant", "content": "Echo: " + last["content"]}
        self._send(200, {"choices": [{"message": message}]})

    def _send(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Config:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class CleanModelTests(unittest.TestCase):
    def entry(self, **changes):
        return {
            "name": "Local",
            "base_url": "http://localhost:11434/v1/",
            "model": "qwen3:8b",
            **changes,
        }

    def test_a_local_model_needs_no_key(self):
        self.assertEqual(
            clean_model(self.entry()),
            {
                "name": "Local",
                "base_url": "http://localhost:11434/v1",
                "model": "qwen3:8b",
                "key": "none",
                "tools": "browsing",
                "vision": False,
            },
        )

    def test_keys_are_never_kept_in_settings(self):
        for key in ("sk-live-secret", "env:", "env:1BAD", "keyring "):
            with self.assertRaises(ValueError):
                clean_model(self.entry(key=key))
        self.assertEqual(clean_model(self.entry(key="env:MY_KEY"))["key"], "env:MY_KEY")

    def test_remote_models_need_https_and_a_plain_address(self):
        for url in (
            "http://example.com/v1",
            "ftp://localhost/v1",
            "https://user:pw@example.com/v1",
            "https://example.com/v1?x=1",
            "localhost:11434",
        ):
            with self.assertRaises(ValueError, msg=url):
                clean_model(self.entry(base_url=url))
        clean_model(self.entry(base_url="https://api.example.com/v1"))

    def test_names_and_models(self):
        for change in (
            {"name": ""},
            {"name": "Codex"},
            {"name": "x" * 41},
            {"model": "two words"},
            {"tools": "root"},
        ):
            with self.assertRaises(ValueError, msg=change):
                clean_model(self.entry(**change))

    def test_browsing_tools_are_real_steed_tools(self):
        from services.agent_tools import TOOLS

        commands = {tool.command for tool in TOOLS}
        self.assertLessEqual(BROWSING_TOOLS, commands)
        self.assertEqual(tool_command("steed_page_click"), "page.click")
        self.assertIsNone(tool_command("rm_rf"))

    def test_environment_keys(self):
        entry = clean_model(self.entry(key="env:STEED_TEST_MODEL_KEY"))
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "STEED_TEST_MODEL_KEY"):
                model_assistant.api_key(entry)
        with unittest.mock.patch.dict("os.environ", {"STEED_TEST_MODEL_KEY": KEY}):
            self.assertEqual(model_assistant.api_key(entry), KEY)
        self.assertIsNone(model_assistant.api_key(clean_model(self.entry())))


class ChatTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    wait_for = fixtures.TaskServiceTests.wait_for

    def setUp(self):
        fixtures.TaskServiceTests.setUp(self)
        FakeChatApi.requests = []
        FakeChatApi.status = 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeChatApi)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.gateway = AgentGateway(self.service, self.directory.name)
        self.config = Config(
            {
                "models": [
                    {
                        "name": "Fake model",
                        "base_url": f"http://127.0.0.1:{self.server.server_port}/v1",
                        "model": "fake-1",
                        "key": "env:STEED_TEST_MODEL_KEY",
                    },
                    {"name": "Broken", "base_url": "nowhere"},
                ]
            }
        )
        registry = SimpleNamespace(
            tasks=self.service,
            agent_gateway=self.gateway,
            set_agent_access=lambda _: None,
            config=self.config,
        )
        self.manager = AgentProviders(registry)
        self.sidebar = AgentSidebar(self.service, providers=self.manager)
        environment = unittest.mock.patch.dict(
            "os.environ", {"STEED_TEST_MODEL_KEY": KEY}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def tearDown(self):
        self.sidebar.detach()
        self.sidebar.close()
        self.manager.close()
        self.gateway.close()
        self.manager.deleteLater()
        fixtures.TaskServiceTests.tearDown(self)

    def replies(self, task, count):
        def texts():
            return [
                m["text"]
                for m in self.service.messages.read(task)["messages"]
                if m["role"] == "agent" and m["state"] == "complete"
            ]

        self.wait_for(lambda: len(texts()) >= count or task not in self.manager.chats)
        return texts()

    def chat(self):
        task = self.manager.new_chat(agent="model:Fake model")
        self.wait_for(
            lambda: (
                self.manager.status(task) == "Fake model ready"
                or task not in self.manager.chats
            )
        )
        self.assertIn(task, self.manager.chats, self.manager.status(task))
        return task

    def test_models_come_first_and_new_chats_use_one(self):
        choices = self.manager.agent_choices()
        self.assertEqual(choices[0], ("model:Fake model", "Fake model"))
        self.assertNotIn("model:Broken", dict(choices))
        self.sidebar._publish()
        self.assertEqual(self.sidebar._new_agent, "model:Fake model")
        self.sidebar.set_agent("codex")
        self.sidebar._publish()
        self.assertEqual(self.sidebar._new_agent, "codex")

    def test_a_model_chats_and_uses_steeds_tools(self):
        task = self.chat()
        self.assertEqual(
            self.service.store.get(task).objective, "Fake model conversation"
        )
        self.service.send_message(task, "Hello")
        self.assertEqual(self.replies(task, 1), ["Echo: Hello"])
        first = FakeChatApi.requests[0]
        self.assertEqual(first["path"], "/v1/chat/completions")
        self.assertEqual(first["authorization"], f"Bearer {KEY}")
        self.assertEqual(first["body"]["model"], "fake-1")
        self.assertEqual(first["body"]["messages"][0]["role"], "system")
        names = {t["function"]["name"] for t in first["body"]["tools"]}
        self.assertIn("steed_page_inspect", names)
        self.assertIn("steed_grant_request", names)
        # Browsing tools only, and never the chat plumbing.
        self.assertNotIn("steed_worker_run", names)
        self.assertNotIn("steed_conversation_reply", names)
        self.assertNotIn("steed_task_start", names)
        self.service.send_message(task, "use a tool")
        self.assertEqual(self.replies(task, 2)[1], "Your browser: ok")
        tool_message = FakeChatApi.requests[-1]["body"]["messages"][-1]
        self.assertEqual(tool_message["tool_call_id"], "call-1")
        # The call went through Steed's task, like any agent's.
        self.assertTrue(
            self.service.store.recent_events(task, limit=50),
        )
        self.service.send_message(task, "made-up tool")
        self.assertEqual(
            self.replies(task, 3)[2],
            "Refused: Error: there is no tool called rm_rf.",
        )
        refusal = FakeChatApi.requests[-1]["body"]["messages"][-1]
        self.assertEqual(refusal["role"], "tool")
        self.assertIn("no tool called rm_rf", refusal["content"])
        self.assertIn(task, self.manager.chats)

    def test_paused_agents_do_nothing_until_resumed(self):
        task = self.chat()
        self.service.set_agents_paused(True)
        self.sidebar.select_task(task)
        self.sidebar.refresh()
        self.assertTrue(self.sidebar.state["paused"])
        self.service.send_message(task, "read my tabs")
        self.assertEqual(self.replies(task, 1), ["Your browser: agents_paused"])
        # Checking its own task still works while paused.
        self.service.send_message(task, "use a tool")
        self.assertEqual(self.replies(task, 2)[1], "Your browser: ok")
        kinds = [e.kind for e in self.service.store.events(task)]
        self.assertIn("agents.paused", kinds)
        self.sidebar._on_command(
            {"action": "resume_agents", "context": self.sidebar._context}
        )
        self.assertFalse(self.service.agents_paused)
        self.service.send_message(task, "read my tabs")
        self.assertEqual(self.replies(task, 3)[2], "Your browser: ok")

    def test_api_errors_end_the_chat_clearly(self):
        FakeChatApi.status = 401
        task = self.chat()
        self.service.send_message(task, "Hello")
        self.wait_for(lambda: task not in self.manager.chats)
        self.assertIn("refused the API key", self.manager.status(task) or "")

    def test_a_missing_key_says_what_to_set(self):
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            task = self.manager.new_chat(agent="model:Fake model")
            self.wait_for(lambda: task not in self.manager.chats)
        self.assertIn("STEED_TEST_MODEL_KEY", self.manager.status(task) or "")


if __name__ == "__main__":
    unittest.main()


class ModelsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_test_support import application

        cls.app = application()

    def test_adding_keeps_the_key_out_of_settings(self):
        from ui.agent_sessions import ModelsDialog

        config = Config()
        dialog = ModelsDialog(config)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.address.text(), "http://localhost:11434/v1")
        dialog.model.setEditText("qwen3:8b")
        dialog.add()
        self.assertEqual(config.get("models")[0]["name"], "qwen3:8b")
        self.assertEqual(config.get("models")[0]["key"], "none")
        dialog.service.setCurrentIndex(
            next(
                i
                for i in range(dialog.service.count())
                if dialog.service.itemText(i) == "OpenAI"
            )
        )
        dialog.model.setEditText("gpt-test")
        dialog.key.setText(KEY)
        with unittest.mock.patch("ui.agent_sessions.save_key") as save:
            dialog.add()
        save.assert_called_once_with("gpt-test", KEY)
        self.assertNotIn(KEY, json.dumps(config.values))
        self.assertEqual(config.get("models")[1]["key"], "keyring")
        self.assertEqual(dialog.key.text(), "")
        dialog.table.setCurrentCell(1, 0)
        with unittest.mock.patch("ui.agent_sessions.forget_key") as forget:
            dialog.remove()
        forget.assert_called_once_with("gpt-test")
        self.assertEqual(len(config.get("models")), 1)


class AgentOnceTests(unittest.TestCase):
    """Watches and the tab organizer ask an added model the same way."""

    @classmethod
    def setUpClass(cls):
        from qt_test_support import application

        cls.app = application()

    def setUp(self):
        FakeChatApi.requests = []
        FakeChatApi.status = 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeChatApi)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.entry = {
            "name": "Fake model",
            "base_url": f"http://127.0.0.1:{self.server.server_port}/v1",
            "model": "fake-1",
        }

    def test_an_added_model_is_asked_first_unless_another_is_chosen(self):
        from services.agent_once import agent_command, agent_name

        config = Config({"models": [self.entry]})
        with unittest.mock.patch("shutil.which", return_value="/usr/bin/claude"):
            chosen = agent_command(config)
            self.assertEqual(agent_name(chosen), "Fake model")
            config.set("watch_agent", "claude")
            self.assertEqual(agent_command(config)[0], "claude")
        with unittest.mock.patch("shutil.which", return_value=None):
            config.set("watch_agent", "")
            config.set("models", [])
            self.assertIsNone(agent_command(config))

    def test_a_model_answers_one_question_without_tools(self):
        from qt_test_support import WAIT_SECONDS

        from services.agent_once import AgentOnce

        answers, failures = [], []
        once = AgentOnce(clean_model(self.entry), "Is this a change?")
        once.answered.connect(answers.append)
        once.failed.connect(failures.append)
        fixtures.TaskServiceTests.wait_for(
            self, lambda: answers or failures, WAIT_SECONDS
        )
        self.assertEqual(answers, ["Echo: Is this a change?"])
        body = FakeChatApi.requests[0]["body"]
        self.assertNotIn("tools", body)
        self.assertEqual(
            body["messages"], [{"role": "user", "content": "Is this a change?"}]
        )
        FakeChatApi.status = 500
        once = AgentOnce(clean_model(self.entry), "Again?")
        once.answered.connect(answers.append)
        once.failed.connect(failures.append)
        fixtures.TaskServiceTests.wait_for(self, lambda: failures, WAIT_SECONDS)
        self.assertEqual(failures, ["Fake model did not answer."])
