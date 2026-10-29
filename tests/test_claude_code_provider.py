"""Claude Code chats over the real child/Qt/gateway path with a synthetic runtime."""

import sys
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

import test_task_services as fixtures

from services.agent_gateway import AgentGateway
from services.agent_providers import AgentProviders
from services.claude_code_provider import ClaudeCodeProvider
from services.task_store import ACTIVE_STATUSES
from ui.agent_sidebar import AgentSidebar


class ClaudeCodeProviderTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    wait_for = fixtures.TaskServiceTests.wait_for

    def setUp(self):
        fixtures.TaskServiceTests.setUp(self)
        self.gateway = AgentGateway(self.service, self.directory.name)
        registry = SimpleNamespace(
            tasks=self.service,
            agent_gateway=self.gateway,
            set_agent_access=lambda _: None,
        )
        self.mode = "normal"
        self.drivers = []

        def factory(parent):
            driver = ClaudeCodeProvider(
                parent,
                command=[
                    sys.executable,
                    str(Path(__file__).parent / "fixtures" / "fake_claude_code.py"),
                    self.mode,
                ],
            )
            self.drivers.append(driver)
            return driver

        self.manager = AgentProviders(registry, factories={"claude": factory})
        self.sidebar = AgentSidebar(self.service, providers=self.manager)
        self.requests = []
        self.manager.requested.connect(
            lambda task, request, payload: self.requests.append(
                (task, request, payload)
            )
        )

    def tearDown(self):
        self.sidebar.detach()
        self.sidebar.close()
        self.manager.close()
        self.gateway.close()
        self.manager.deleteLater()
        fixtures.TaskServiceTests.tearDown(self)

    def chat(self, mode="normal", model=None):
        self.mode = mode
        task = self.manager.new_chat(model=model, agent="claude")
        self.sidebar.select_task(task)
        self.wait_for(
            lambda: (
                self.manager.status(task) == "Claude Code ready"
                or task not in self.manager.chats
            )
        )
        return task

    def agent_texts(self, task, count=1):
        def texts():
            return [
                m["text"]
                for m in self.service.messages.read(task)["messages"]
                if m["role"] == "agent" and m["state"] == "complete"
            ]

        self.wait_for(lambda: len(texts()) >= count)
        return texts()

    def test_turns_stream_once_and_report_the_runtime_model(self):
        task = self.chat()
        self.assertEqual(self.manager.status(task), "Claude Code ready")
        self.assertEqual(
            self.service.store.get(task).objective, "Claude Code conversation"
        )
        self.service.send_message(task, "First")
        self.service.send_message(task, "Second")
        self.assertEqual(
            self.agent_texts(task, 2),
            ["Synthetic reply: First", "Synthetic reply: Second"],
        )
        model = self.manager.model_info(task)
        self.assertEqual(
            (model["model"], model["provider"], model["agent"]),
            ("claude-default", "Anthropic", "Claude Code"),
        )
        activity = self.sidebar._activity(True)
        self.assertEqual(activity["title"], "Claude Code is ready")
        self.assertIn("reported by Claude Code", activity["model"])

    def test_selected_model_and_unstreamed_replies(self):
        task = self.chat("no-partial", model="haiku")
        self.service.send_message(task, "Plain")
        self.assertEqual(self.agent_texts(task), ["Synthetic reply: Plain"])
        self.assertEqual(self.manager.model_info(task)["model"], "claude-haiku")

    def test_subagent_output_is_not_this_chats_reply(self):
        task = self.chat("subagent")
        self.service.send_message(task, "Delegate")
        texts = self.agent_texts(task)
        self.assertEqual(texts, ["Synthetic reply: Delegate"])
        self.assertNotIn("CHILD", str(self.service.messages.read(task)))

    def test_native_permission_once_session_and_deny(self):
        for decision, expected in (
            ({"decision": "accept"}, "Permission allow (once)"),
            ({"decision": "acceptForSession"}, "Permission allow (session)"),
            ({"decision": "decline"}, "Permission deny (once)"),
        ):
            with self.subTest(decision=decision):
                task = self.chat("permission")
                self.service.send_message(task, "Run it")
                self.wait_for(
                    lambda task=task: any(r[0] == task for r in self.requests)
                )
                _, request_id, payload = next(r for r in self.requests if r[0] == task)
                self.assertEqual(payload["agent"], "Claude Code")
                self.assertEqual(
                    payload["method"], "item/commandExecution/requestApproval"
                )
                self.assertEqual(payload["params"]["command"], "echo synthetic")
                self.assertIn(
                    "acceptForSession", payload["params"]["availableDecisions"]
                )
                self.assertEqual(self.manager.activity(task)["state"], "waiting")
                self.manager.respond(task, request_id, decision)
                self.assertEqual(self.agent_texts(task), [expected])
                self.manager._stop(task)

    def test_ask_user_question_returns_answers_by_question(self):
        task = self.chat("question")
        self.service.send_message(task, "Ask me")
        self.wait_for(lambda: bool(self.requests))
        _, request_id, payload = self.requests[0]
        self.assertEqual(payload["method"], "tool/requestUserInput")
        question = payload["params"]["questions"][0]
        self.assertEqual(question["question"], "Which colour?")
        self.assertEqual([o["label"] for o in question["options"]], ["Blue", "Red"])
        self.manager.respond(
            task, request_id, {"answers": {"0": {"answers": ["Blue"]}}}
        )
        self.assertEqual(self.agent_texts(task), ['Answer: {"Which colour?": "Blue"}'])

    def test_unsupported_host_requests_are_refused_without_stopping(self):
        task = self.chat("hook")
        self.service.send_message(task, "Hooked")
        self.assertEqual(self.agent_texts(task), ["Synthetic reply: Hooked"])
        self.assertIn(task, self.manager.chats)

    def test_failures_stop_the_chat_and_cancel_its_task(self):
        for mode, text in (
            ("error-result", "Synthetic failure"),
            ("disconnect", "Claude Code"),
            ("mcp-failed", "could not connect to Steed"),
        ):
            with self.subTest(mode=mode):
                task = self.chat(mode)
                if mode != "mcp-failed":
                    self.service.send_message(task, "Go")
                self.wait_for(lambda task=task: task not in self.manager.chats)
                self.assertIn(text, self.manager.status(task))
                self.assertNotIn(self.service.store.get(task).status, ACTIVE_STATUSES)

    def test_missing_cli_explains_how_to_install(self):
        provider = ClaudeCodeProvider(command=None)
        failures = []
        provider.failed.connect(failures.append)
        with unittest.mock.patch("shutil.which", return_value=None):
            provider.start(self.directory.name, Path(self.directory.name) / "b.json")
        self.assertIn("Install Claude Code", failures[0])
        provider.deleteLater()

    def test_sidebar_switches_agent_and_resets_model_choice(self):
        self.sidebar.set_model("gpt-5.4")
        self.sidebar.set_agent("claude")
        self.assertIsNone(self.sidebar._new_model)
        self.sidebar.set_model("gpt-5.4")
        self.assertIsNone(self.sidebar._new_model)
        self.sidebar.set_model("opus")
        self.assertEqual(self.sidebar._new_model, "opus")
        self.sidebar.set_agent("unknown")
        self.assertEqual(self.sidebar._new_agent, "claude")


if __name__ == "__main__":
    unittest.main()
