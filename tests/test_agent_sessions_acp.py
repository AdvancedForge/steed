"""Continuing saved Claude Code sessions, and chats with ACP agents."""

import json
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

import test_task_services as fixtures

import services.claude_sessions as sessions
from services.agent_gateway import AgentGateway
from services.agent_providers import AgentProviders
from services.claude_code_provider import ClaudeCodeProvider
from ui.agent_sidebar import AgentSidebar

FIXTURES = Path(__file__).parent / "fixtures"
FIRST = "11111111-2222-3333-4444-555555555555"
SECOND = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def write_session(folder, session_id, cwd, records):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    lines = [json.dumps({"type": "mode", "sessionId": session_id})]
    for kind, value in records:
        if kind == "user":
            lines.append(
                json.dumps(
                    {
                        "type": "user",
                        "cwd": cwd,
                        "sessionId": session_id,
                        "message": {"role": "user", "content": value},
                    }
                )
            )
        else:
            lines.append(json.dumps({"type": "ai-title", "aiTitle": value}))
    path.write_text("\n".join(lines) + "\n")
    return path


class Config:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class SessionListTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "projects"
        self.work = Path(self.directory.name) / "work"
        self.work.mkdir()

    def test_titles_folders_order_and_what_is_left_out(self):
        old = write_session(
            self.root / "-work",
            FIRST,
            str(self.work),
            [("user", "/compact"), ("user", "Fix the parser please"), ("title", "")],
        )
        write_session(
            self.root / "-work",
            SECOND,
            str(self.work),
            [
                ("user", [{"type": "text", "text": "hello"}]),
                ("title", "Parser rewrite"),
            ],
        )
        # Steed's own private chats and odd files are not sessions to continue.
        write_session(
            self.root / "-tmp-steed-codex-x",
            SECOND.replace("a", "c"),
            "/tmp",
            [("user", "x")],
        )
        (self.root / "-work" / "notes.jsonl").write_text("{}\n")
        stamp = time.time() - 3600
        import os

        os.utime(old, (stamp, stamp))
        found = sessions.list_sessions(self.root)
        self.assertEqual([s["id"] for s in found], [SECOND, FIRST])
        self.assertEqual(found[0]["title"], "Parser rewrite")
        self.assertEqual(found[1]["title"], "Fix the parser please")  # Not /compact.
        self.assertEqual(found[1]["cwd"], str(self.work))
        self.assertEqual(sessions.find_session(FIRST, self.root)["id"], FIRST)
        self.assertIsNone(sessions.find_session("../../etc/passwd", self.root))
        self.assertIsNone(sessions.find_session(SECOND.replace("e", "f"), self.root))
        # A session whose folder is gone cannot be continued.
        write_session(
            self.root / "-gone",
            SECOND.replace("a", "b"),
            "/no/such/folder",
            [("user", "x")],
        )
        self.assertIsNone(sessions.find_session(SECOND.replace("a", "b"), self.root))


class ChatTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    wait_for = fixtures.TaskServiceTests.wait_for

    def setUp(self):
        fixtures.TaskServiceTests.setUp(self)
        self.gateway = AgentGateway(self.service, self.directory.name)
        self.config = Config()
        registry = SimpleNamespace(
            tasks=self.service,
            agent_gateway=self.gateway,
            set_agent_access=lambda _: None,
            config=self.config,
        )

        def claude(parent):
            return ClaudeCodeProvider(
                parent,
                command=[
                    sys.executable,
                    str(FIXTURES / "fake_claude_code.py"),
                    "resume",
                ],
            )

        self.manager = AgentProviders(registry, factories={"claude": claude})
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

    def replies(self, task, count):
        def texts():
            return [
                m["text"]
                for m in self.service.messages.read(task)["messages"]
                if m["role"] == "agent" and m["state"] == "complete"
            ]

        self.wait_for(lambda: len(texts()) >= count)
        return texts()

    def ready(self, task, name):
        self.wait_for(
            lambda: (
                self.manager.status(task) == f"{name} ready"
                or task not in self.manager.chats
            )
        )

    def test_a_saved_claude_code_session_continues_in_its_folder(self):
        root = Path(self.directory.name) / "projects"
        work = Path(self.directory.name) / "project"
        work.mkdir()
        write_session(
            root / "-project", FIRST, str(work), [("user", "Hi"), ("title", "Old chat")]
        )
        with unittest.mock.patch.object(sessions, "projects_root", return_value=root):
            with self.assertRaises(ValueError):
                self.manager.new_chat(agent="claude", resume=SECOND)
            with self.assertRaises(ValueError):  # Only Claude Code resumes.
                self.manager.new_chat(agent="codex", resume=FIRST)
            task = self.manager.new_chat(agent="claude", resume=FIRST)
        self.ready(task, "Claude Code")
        self.assertEqual(
            self.service.store.get(task).objective, "Claude Code: Old chat"
        )
        self.service.send_message(task, "and now?")
        (reply,) = self.replies(task, 1)
        self.assertEqual(reply, f"Resumed {FIRST} in {work}: and now?")

    def acp_chat(self, mode="normal"):
        self.config.set(
            "acp_agents",
            [
                {
                    "name": "Fake ACP",
                    "command": [
                        sys.executable,
                        str(FIXTURES / "fake_acp_agent.py"),
                        mode,
                    ],
                },
                {"name": "Broken", "command": "not a list"},
            ],
        )
        self.assertIn(("acp:Fake ACP", "Fake ACP"), self.manager.agent_choices())
        self.assertNotIn("acp:Broken", dict(self.manager.agent_choices()))
        self.sidebar.set_agent("acp:Fake ACP")
        self.assertEqual(self.sidebar._new_agent, "acp:Fake ACP")
        task = self.manager.new_chat(agent="acp:Fake ACP")
        self.ready(task, "Fake ACP")
        return task

    def test_acp_agents_chat_ask_permission_and_are_refused_files(self):
        task = self.acp_chat()
        self.assertEqual(
            self.service.store.get(task).objective, "Fake ACP conversation"
        )
        self.service.send_message(task, "Hello")
        self.assertEqual(
            self.replies(task, 2),
            ["Synthetic reply: Hello (with Steed's note)", "Done."],
        )
        self.service.send_message(task, "Again")  # The note goes with the first only.
        self.assertEqual(self.replies(task, 4)[2], "Synthetic reply: Again")
        self.service.send_message(task, "ask")
        self.wait_for(lambda: self.requests)
        _, request_id, payload = self.requests[-1]
        self.assertEqual(payload["agent"], "Fake ACP")
        self.assertEqual(payload["params"]["permissions"]["tool"], "Run tests")
        self.assertEqual(
            payload["params"]["availableDecisions"],
            ["accept", "acceptForSession", "decline"],
        )
        self.manager.respond(task, request_id, {"decision": "acceptForSession"})
        self.assertEqual(self.replies(task, 5)[4], "Permission: always")
        self.service.send_message(task, "files")
        self.assertEqual(self.replies(task, 6)[5], "fs refused: -32601")
        self.assertIn(task, self.manager.chats)  # Still going.

    def test_acp_sign_in_and_version_problems_end_the_chat_clearly(self):
        failures = []
        self.manager.registry.tasks.changed.connect(lambda *_: None)
        for mode, expected in (("auth", "Sign in"), ("version", "ACP version 2")):
            task = self.acp_chat(mode)
            self.wait_for(lambda task=task: task not in self.manager.chats)
            status = self.manager.status(task) or ""
            failures.append((expected, status))
        for expected, status in failures:
            self.assertIn(expected, status)


if __name__ == "__main__":
    unittest.main()
