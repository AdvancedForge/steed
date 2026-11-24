"""Real child/Qt/gateway chat lifecycle with a deterministic external runtime."""

import sqlite3
import sys
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_task_services as fixtures
from PyQt6.QtCore import QProcess

from services.agent_gateway import AgentGateway
from services.agent_providers import AgentProviders
from services.codex_provider import CodexProvider
from ui.agent_sidebar import AgentSidebar


class AgentProviderTests(unittest.TestCase):
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
            driver = CodexProvider(
                parent,
                command=[
                    sys.executable,
                    str(Path(__file__).parent / "fixtures" / "fake_codex_server.py"),
                    self.mode,
                ],
            )
            self.drivers.append(driver)
            return driver

        self.manager = AgentProviders(registry, factory=factory)
        self.sidebar = AgentSidebar(self.service, providers=self.manager)

    def tearDown(self):
        self.sidebar.detach()
        self.sidebar.close()
        self.manager.close()
        self.gateway.close()
        self.manager.deleteLater()
        fixtures.TaskServiceTests.tearDown(self)

    def chat(self, mode="normal"):
        self.mode = mode
        task = self.manager.new_chat()
        self.sidebar.select_task(task)
        self.wait_for(
            lambda: (
                self.manager.status(task) == "Codex ready"
                or task not in self.manager.chats
            )
        )
        return task

    def send(self, text):
        self.sidebar.set_draft(text)
        self.sidebar.send_message()

    def complete(self, task, count=1):
        self.wait_for(
            lambda: (
                len(
                    [
                        m
                        for m in self.messages(task)
                        if m["role"] == "agent" and m["state"] == "complete"
                    ]
                )
                == count
            )
        )

    def messages(self, task):
        return self.service.messages.read(task)["messages"]

    def test_a_kept_codex_chat_continues_its_saved_thread(self):
        settings = Path(self.directory.name) / "settings"
        codex_home = Path(self.directory.name) / "codex"
        self.manager.registry.config = SimpleNamespace(
            config_dir=settings, get=lambda key, default=None: default
        )
        with unittest.mock.patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}):
            first = self.chat("keep")
            self.send("Remember the plan")
            self.complete(first)
            saved = self.manager.saved_session(first)
            self.assertEqual(saved["agent"], "codex")
            folder = settings / "agent_chats" / first
            self.assertEqual(saved["cwd"], str(folder))
            # Not offered until Codex has the thread saved.
            self.manager.close()
            self.assertIsNone(self.manager.continuable(first))
            day = codex_home / "sessions" / "2026" / "11" / "16"
            day.mkdir(parents=True)
            (day / f"rollout-2026-11-16T00-00-00-{saved['session_id']}.jsonl").touch()
            self.sidebar.select_task(first)
            self.sidebar.refresh()
            self.assertTrue(self.sidebar.state["can_continue"])
            self.mode = "continue"
            self.sidebar.continue_chat()
            second = self.sidebar.state["task_id"]
            self.assertNotEqual(second, first)
            self.wait_for(lambda: self.manager.status(second) == "Codex ready")
            self.send("Where were we?")
            self.complete(second)
            self.assertEqual(
                self.messages(second)[-1]["text"],
                f"Resumed {saved['session_id']} in {folder}: Where were we?",
            )
            self.assertEqual(self.service.store.continuations(), {first: second})

    def test_sidebar_starts_turn_and_continues_same_task(self):
        task = self.chat()
        self.assertTrue(self.sidebar.state["active"])
        self.send("First prompt")
        self.complete(task)
        self.send("Follow-up")
        self.complete(task, 2)
        self.assertEqual(
            [m["text"] for m in self.messages(task)],
            [
                "First prompt",
                "Synthetic reply: First prompt",
                "Follow-up",
                "Synthetic reply: Follow-up",
            ],
        )
        self.assertEqual(self.service.store.grants(task), [])
        self.assertIn("Agent fetched", self.sidebar.state["status"])
        self.assertEqual(len(self.drivers), 1)

    def test_a_turn_adds_up_the_tokens_codex_reported_without_a_cost(self):
        task = self.chat()
        self.send("Count me")
        self.complete(task)
        self.wait_for(
            lambda: self.service.store.recent_events(task, kinds=("agent.usage",))
        )
        (event,) = self.service.store.recent_events(task, kinds=("agent.usage",))
        payload = dict(event.payload)
        payload.pop("model", None)
        self.assertEqual(
            payload,
            {
                "input_tokens": 100,
                "output_tokens": 14,
                "cached_tokens": 80,
                "agent": "Codex",
            },
        )

    def test_clock_optional_rpc_and_child_events_keep_followups_connected(self):
        for mode in ("clock", "unsupported-rpc", "child-events"):
            with self.subTest(mode=mode):
                task = self.chat(mode)
                self.send("Begin working")
                self.complete(task)
                self.send("Continue")
                self.complete(task, 2)
                self.wait_for(
                    lambda task=task: self.manager.status(task) == "Codex ready"
                )
                self.assertIn(task, self.manager.chats)
                self.assertNotIn("CHILD_PRIVATE", self.sidebar.transcript_text)
                self.assertEqual(self.service.store.grants(task), [])
                self.service.cancel_task(task)

    def test_native_tool_event_changes_the_visible_working_state(self):
        task = self.chat("tool-work")
        self.send("Use a tool")
        self.wait_for(lambda: "working" in self.sidebar.state["activity"]["title"])
        self.assertTrue(self.sidebar.state["activity"]["busy"])
        self.complete(task)
        self.wait_for(lambda: self.manager.status(task) == "Codex ready")
        self.assertIn("ready", self.sidebar.state["activity"]["title"])
        self.assertFalse(self.sidebar.state["activity"]["busy"])

    def test_foreign_thread_reply_is_never_imported_or_treated_as_our_turn(self):
        task = self.chat("wrong-thread")
        self.send("Owned prompt")
        self.wait_for(lambda: self.manager.status(task) == "Codex ready")
        self.assertIn(task, self.manager.chats)
        self.assertFalse(any(m["role"] == "agent" for m in self.messages(task)))
        self.assertEqual(self.service.store.grants(task), [])

    def test_current_question_method_uses_the_existing_answer_popup(self):
        task = self.chat("question-current")
        self.send("Ask me before working")
        self.wait_for(lambda: self.manager.chats[task]["requests"])
        self.assertEqual(self.manager.activity(task)["state"], "waiting")
        self.assertIn("needs your input", self.sidebar.state["activity"]["title"])
        self.sidebar.show_requests()
        dialog = self.sidebar._request_dialogs[(task, "native-request")]
        dialog.answers["choice"][0].setCurrentIndex(1)
        dialog.allow.click()
        self.complete(task)
        self.assertEqual(self.service.store.grants(task), [])

    def test_a_question_pops_up_even_from_a_chat_not_showing(self):
        task = self.chat("question-current")
        self.sidebar._task_id = None  # Another chat (or none) is showing.
        self.service.send_message(task, "Ask me before working")
        self.wait_for(lambda: (task, "native-request") in self.sidebar._request_dialogs)
        dialog = self.sidebar._request_dialogs[(task, "native-request")]
        dialog.answers["choice"][0].setCurrentIndex(1)
        dialog.allow.click()
        self.complete(task)

    def test_working_states_and_queue_are_visible_without_task_details(self):
        task = self.chat("hang")
        self.send("Start")
        self.assertEqual(self.manager.activity(task)["state"], "sending")
        self.assertEqual(
            self.sidebar.state["messages"][0]["delivery"], "Awaiting acceptance"
        )
        self.assertIn("Sending", self.sidebar.state["activity"]["title"])
        self.manager._accepted(task, self.messages(task)[0]["message_id"])
        self.assertIn("thinking", self.sidebar.state["activity"]["title"])
        self.drivers[-1].activity.emit("working")
        self.assertIn("working", self.sidebar.state["activity"]["title"])
        self.assertTrue(self.sidebar.state["activity"]["busy"])
        self.send("Follow-up")
        self.assertEqual(
            self.sidebar.state["messages"][-1]["delivery"], "Queued for agent"
        )
        self.assertIn("1 follow-up", self.sidebar.state["activity"]["hint"])
        self.drivers[-1].activity.emit("replying")
        self.assertIn("replying", self.sidebar.state["activity"]["title"])
        self.sidebar.cancel_task()
        self.assertFalse(self.sidebar.state["activity"]["busy"])

    def test_busy_queue_keeps_order_and_does_not_duplicate(self):
        task = self.chat()
        first = self.service.send_message(task, "First")
        self.service.user_message_sent.emit(task, first)
        self.service.send_message(task, "Second")
        self.service.send_message(task, "Third")
        self.complete(task, 3)
        self.assertEqual(
            [m["text"] for m in self.messages(task) if m["role"] == "agent"],
            [
                "Synthetic reply: First",
                "Synthetic reply: Second",
                "Synthetic reply: Third",
            ],
        )

    def test_message_sent_while_codex_finishes_connecting_is_delivered(self):
        ready = []
        with patch.object(self.manager, "_ready", ready.append):
            task = self.manager.new_chat()
            self.wait_for(lambda: ready and task in self.service.external_tasks)
        self.assertFalse(self.manager.can_send(task))
        # Panel instructions reach a connected conversation without the UI gate.
        self.service.send_message(task, "Early instruction")
        self.manager._ready(task)
        self.complete(task)
        self.assertEqual(
            [m["text"] for m in self.messages(task)],
            ["Early instruction", "Synthetic reply: Early instruction"],
        )

    def test_events_before_turn_response_are_retained(self):
        task = self.chat("early-events")
        self.send("Early")
        self.complete(task)
        self.assertEqual(self.messages(task)[-1]["text"], "Synthetic reply: Early")

    def test_long_utf8_reply_is_split_without_corruption(self):
        task = self.chat("long")
        self.send("Long")
        self.complete(task, 2)
        replies = [m for m in self.messages(task) if m["role"] == "agent"]
        self.assertEqual("".join(m["text"] for m in replies), "🌍" * 2500)
        self.assertTrue(all(len(m["text"].encode()) <= 8192 for m in replies))

    def test_clear_discards_queued_prompts(self):
        task = self.chat()
        self.send("Already sent")
        self.service.send_message(task, "Queued then cleared")
        self.service.clear_messages(task)
        self.wait_for(
            lambda: (
                self.manager.status(task) == "Codex ready"
                or task not in self.manager.chats
            )
        )
        self.assertFalse(
            any(
                m["role"] == "agent" and "Queued then cleared" in m["text"]
                for m in self.messages(task)
            )
        )

    def test_clear_during_stream_does_not_restore_text_or_end_task(self):
        task = self.chat("slow")
        self.send("Stream then clear")
        self.wait_for(
            lambda: any(m["state"] == "streaming" for m in self.messages(task))
        )
        self.service.send_message(task, "Queued before clear")
        self.service.clear_messages(task)
        self.wait_for(lambda: self.manager.status(task) == "Codex ready")
        self.assertTrue(all(m["state"] == "cleared" for m in self.messages(task)))
        self.assertIn(task, self.manager.chats)
        self.send("New prompt")
        self.complete(task)
        self.assertIn("Synthetic reply: New prompt", self.sidebar.transcript_text)

    def test_cancel_stops_child_and_keeps_history(self):
        task = self.chat()
        self.send("Explicitly shared")
        self.sidebar.cancel_task()
        self.wait_for(
            lambda: self.drivers[0].process.state() == QProcess.ProcessState.NotRunning
        )
        self.assertNotIn(task, self.manager.chats)
        self.assertEqual(self.service.store.get(task).status, "cancelled")
        self.assertEqual(self.messages(task)[0]["text"], "Explicitly shared")
        self.assertFalse(self.sidebar.state["active"])

    def test_incognito_disables_new_chat_without_cancelling_work(self):
        task = self.chat()
        self.sidebar.set_private(True)
        self.assertFalse(self.sidebar.state["available"])
        self.assertFalse(self.sidebar.state["active"])
        self.assertIn(task, self.manager.chats)

    def test_signin_is_external_and_failure_retains_readonly_task(self):
        task = self.chat("signin")
        self.assertNotIn(task, self.manager.chats)
        self.assertIn("codex login", self.sidebar.state["status"])
        self.assertEqual(self.service.store.get(task).status, "cancelled")
        self.assertFalse(self.sidebar.state["active"])

    def test_extra_server_and_wrong_task_fail_closed(self):
        for mode in ("extra-server", "wrong-task"):
            with self.subTest(mode=mode):
                task = self.chat(mode)
                self.assertNotIn(task, self.manager.chats)
                self.assertEqual(self.service.store.get(task).status, "cancelled")
                self.assertFalse(self.sidebar.state["active"])

    def test_native_permission_settings_are_preserved(self):
        task = self.chat("unsafe-profile")
        self.assertIn(task, self.manager.chats)
        self.assertEqual(self.service.store.grants(task), [])
        self.send("Native capabilities retained")
        self.wait_for(lambda: self.messages(task)[-1]["state"] == "complete")

    def test_native_approval_popup_does_not_grant_steed_access(self):
        task = self.chat("approval")
        self.send("Run a native tool")
        self.wait_for(lambda: self.manager.chats[task]["requests"])
        self.sidebar.show_requests()
        dialog = self.sidebar._request_dialogs[(task, "native-request")]
        self.assertIn("synthetic-native-command", dialog.params["command"])
        dialog.session.click()
        self.complete(task)
        self.assertEqual(self.service.store.grants(task), [])
        self.assertEqual(self.manager.chats[task]["requests"], {})

    def test_native_question_returns_answer_without_storing_it_in_chat(self):
        task = self.chat("question")
        self.send("Ask me")
        self.wait_for(lambda: self.manager.chats[task]["requests"])
        self.sidebar.show_requests()
        dialog = self.sidebar._request_dialogs[(task, "native-request")]
        dialog.allow.click()
        self.assertIn("Answer each", dialog.error.text())
        dialog.answers["choice"][0].setCurrentIndex(1)
        dialog.allow.click()
        self.complete(task)
        self.assertEqual(self.service.store.grants(task), [])
        self.assertFalse(any(m["text"] == "Plain" for m in self.messages(task)))

    def test_native_permission_deny_and_cancel_cleanup(self):
        task = self.chat("permission")
        self.send("Request native network")
        self.wait_for(lambda: self.manager.chats[task]["requests"])
        self.sidebar.show_requests()
        dialog = self.sidebar._request_dialogs[(task, "native-request")]
        dialog.reject()
        self.complete(task)
        self.assertEqual(self.service.store.grants(task), [])
        self.send("Another request")
        self.wait_for(lambda: self.manager.chats[task]["requests"])
        self.sidebar.show_requests()
        self.service.cancel_task(task)
        self.assertNotIn(task, self.manager.chats)
        self.assertEqual(self.sidebar._request_dialogs, {})

    def test_protocol_failures_stop_without_replaying_queued_messages(self):
        for mode in (
            "disconnect",
            "malformed",
            "oversized",
            "host-request",
            "wrong-turn",
        ):
            with self.subTest(mode=mode):
                task = self.chat(mode)
                self.send("Prompt")
                self.service.send_message(task, "Queued")
                self.wait_for(lambda task=task: task not in self.manager.chats)
                # Every way a turn is cut off (a lost connection included) is
                # recorded as interrupted, never as a cancel you did not ask for.
                self.assertEqual(self.service.store.get(task).status, "interrupted")
                self.assertEqual(self.messages(task)[1]["text"], "Queued")
                self.assertNotIn(
                    "synthetic-private-command", self.sidebar.state["status"]
                )
                self.assertEqual(self.service.store.grants(task), [])

    def test_shutdown_mid_turn_is_recorded_as_interrupted_not_cancelled(self):
        task = self.chat("hang")
        self.send("Cut off by shutdown")
        self.manager.close()
        self.assertEqual(self.service.store.get(task).status, "interrupted")
        self.assertEqual(self.manager.connection(task)["reason"], "closed")
        self.assertEqual(self.manager.connection(task)["live"], False)
        self.sidebar.refresh()
        activity = self.sidebar.state["activity"]
        self.assertEqual(activity["title"], "Chat closed")
        self.assertIn("Steed closed this chat", activity["hint"])
        self.assertIn(
            "Cut off by shutdown", [m["text"] for m in self.sidebar.state["messages"]]
        )
        self.assertFalse(self.sidebar.state["active"])
        self.assertEqual(len(self.messages(task)), 1)

    def test_a_cut_off_turn_is_never_resent_or_duplicated(self):
        task = self.chat("hang")
        self.send("Delivered once")
        self.service.send_message(task, "Queued for a later turn")
        before = len(self.messages(task))
        self.manager.close()
        with self.assertRaises(ValueError):
            self.service.send_message(task, "Resent after the interruption")
        self.assertEqual(len(self.messages(task)), before)
        self.assertEqual(
            [m["text"] for m in self.messages(task)],
            ["Delivered once", "Queued for a later turn"],
        )
        self.assertEqual(self.service.messages.read_sequence(task), 0)

    def test_a_partial_reply_is_kept_and_labelled_interrupted(self):
        task = self.chat("slow")
        self.send("Streamed then cut")
        self.wait_for(
            lambda: any(m["state"] == "streaming" for m in self.messages(task))
        )
        partial = self.messages(task)[-1]["text"]
        self.manager.close()
        self.wait_for(lambda: self.messages(task)[-1]["state"] == "interrupted")
        self.sidebar.refresh()
        shown = self.sidebar.state["messages"][-1]
        self.assertEqual(
            shown["state"], "interrupted"
        )  # "interrupted before finishing"
        self.assertIn(partial, shown["text"])
        self.assertEqual(self.service.store.get(task).status, "interrupted")

    def test_an_interrupted_task_without_a_recorded_reason_says_so(self):
        task = self.chat()
        self.send("Never finished")
        # A crash or restart ends the turn with no provider record at all.
        self.service.store.transition(task, "interrupted")
        self.manager._stop(task)
        self.sidebar.refresh()
        activity = self.sidebar.state["activity"]
        self.assertEqual(activity["title"], "Chat interrupted")
        self.assertIn("Start a new chat", activity["hint"])
        self.assertFalse(self.sidebar.state["active"])

    def test_a_failed_start_without_a_turn_is_not_called_interrupted(self):
        task = self.chat("signin")
        self.assertNotIn(task, self.manager.chats)
        self.assertEqual(self.service.store.get(task).status, "cancelled")
        self.assertEqual(self.messages(task), [])

    def test_failed_chat_explains_the_reason_and_when_the_agent_last_reported(self):
        task = self.chat("malformed")
        self.send("Prompt")
        self.service.send_message(task, "Queued but unread")
        self.wait_for(lambda: task not in self.manager.chats)
        connection = self.manager.connection(task)
        self.assertFalse(connection["live"])
        self.assertEqual(connection["reason"], "failed")
        self.assertEqual(connection["queued"], 0)
        self.assertIsInstance(connection["reported_at"], float)
        self.sidebar.refresh()
        activity = self.sidebar.state["activity"]
        self.assertEqual("Stopped after an error", activity["title"])
        self.assertIn("Start a new chat", activity["hint"])
        self.assertEqual(self.service.store.get(task).status, "interrupted")
        self.assertIn("Last agent update", activity["hint"])
        self.assertIn("this chat has stopped", self.sidebar.state["status"])
        self.assertNotIn("waiting for the agent", self.sidebar.state["status"])

    def test_ended_and_disconnected_chats_are_not_reported_as_failures(self):
        ended = self.chat()
        self.service.cancel_task(ended)
        self.wait_for(lambda: ended not in self.manager.chats)
        self.assertEqual(self.manager.connection(ended)["reason"], "ended")
        self.sidebar.select_task(ended)
        self.sidebar.refresh()
        self.assertEqual(self.sidebar.state["activity"]["title"], "Chat ended")
        self.assertIn("Start a new chat", self.sidebar.state["activity"]["hint"])
        closed = self.chat()
        self.manager.close()
        self.assertEqual(self.manager.connection(closed)["reason"], "closed")
        self.sidebar.select_task(closed)
        self.sidebar.refresh()
        self.assertEqual(self.sidebar.state["activity"]["title"], "Chat closed")
        # Steed closing an idle chat is not a cancellation either.
        self.assertEqual(self.service.store.get(closed).status, "interrupted")

    def test_live_chat_reports_the_last_runtime_update_time(self):
        task = self.chat()
        connection = self.manager.connection(task)
        self.assertTrue(connection["live"])
        self.assertEqual(connection["reason"], "")
        self.assertEqual(connection["detail"], "Codex ready")
        self.assertIsInstance(connection["reported_at"], float)
        self.sidebar.refresh()
        self.assertIn("Last agent update", self.sidebar.state["activity"]["hint"])
        self.send("Still connected")
        self.complete(task)
        self.assertIn(task, self.manager.chats)

    def test_client_without_a_reported_update_shows_no_agent_time(self):
        task = self.chat()
        stub = SimpleNamespace(
            chats={},
            status=lambda _: "",
            can_send=lambda _: True,
            activity=lambda _: None,
            model_info=lambda _: None,
            connection=lambda _: {
                "live": False,
                "reason": "",
                "detail": "",
                "reported_at": None,
                "queued": 0,
                "requests": 0,
            },
        )
        self.sidebar.providers = stub
        try:
            activity = self.sidebar._activity(True)
            self.assertNotIn("Last agent update", activity["hint"])
        finally:
            self.sidebar.providers = self.manager
            self.service.cancel_task(task)

    def test_two_tasks_are_isolated_and_limit_is_enforced(self):
        first, second = self.chat(), self.chat()
        with self.assertRaises(ValueError):
            self.manager.new_chat()
        self.send("Second only")
        self.complete(second)
        self.assertEqual(self.messages(first), [])
        self.manager.close()
        self.assertEqual(self.manager.chats, {})

    def test_timeout_stops_without_acknowledging_or_retrying(self):
        task = self.chat("hang")
        self.send("Uncertain delivery")
        driver = self.drivers[-1]
        driver.pending = {
            key: (callback, 0) for key, (callback, _) in driver.pending.items()
        }
        driver._timeouts()
        self.wait_for(lambda: task not in self.manager.chats)
        self.assertEqual(self.service.messages.read_sequence(task), 0)
        self.assertEqual(self.service.store.get(task).status, "interrupted")
        self.assertEqual(len(self.drivers), 1)

    def test_acknowledgement_storage_failure_does_not_escape_signal(self):
        task = self.chat("hang")
        self.send("Synthetic prompt")
        driver = self.drivers[-1]
        message_id = self.messages(task)[0]["message_id"]
        with patch.object(
            self.service.messages,
            "acknowledge",
            side_effect=sqlite3.OperationalError("Synthetic database failure"),
        ):
            driver.accepted.emit(message_id)
        self.assertTrue(self.service.failure)
        self.assertNotIn(task, self.manager.chats)

    def test_new_binding_file_is_private_and_removed_once_connected(self):
        self.mode = "normal"
        task = self.manager.new_chat()
        path = self.manager.chats[task]["binding_file"]
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.wait_for(lambda: self.manager.status(task) == "Codex ready")
        self.assertFalse(path.exists())
        self.assertEqual(self.gateway.bindings, {})

    def test_missing_dependency_creates_no_task_or_connection(self):
        from unittest.mock import patch

        with patch(
            "services.agent_providers.importlib.util.find_spec", return_value=None
        ):
            with self.assertRaises(RuntimeError):
                self.manager.new_chat()
        self.assertEqual(self.service.store.list_tasks(), [])

    def test_trusted_model_choice_is_scoped_to_this_chat(self):
        self.sidebar.set_model("gpt-5.4")
        self.sidebar.start_chat()
        task = next(iter(self.manager.chats))
        self.wait_for(lambda: self.manager.status(task) == "Codex ready")
        self.assertEqual(self.drivers[-1].model, "gpt-5.4")
        self.assertIn(
            "synthetic-runtime-model", self.sidebar.state["activity"]["model"]
        )
        self.assertNotIn("gpt-5.4", self.sidebar.state["activity"]["model"])
        self.sidebar.set_model(None)
        self.assertEqual(self.drivers[-1].model, "gpt-5.4")
        self.assertIn(
            "synthetic-runtime-model", self.sidebar.state["activity"]["model"]
        )
        with self.assertRaises(ValueError):
            self.manager.new_chat(model="invalid model/argument")
        self.assertEqual(len(self.service.store.list_tasks()), 1)

    def test_unreported_or_invalid_model_is_never_inferred_from_selection(self):
        for mode in ("model-unreported", "model-invalid"):
            with self.subTest(mode=mode):
                task = self.chat(mode)
                # Nothing is shown rather than a guess from the selection.
                self.assertEqual(self.sidebar.state["activity"]["model"], "")
                self.send("Still connected")
                self.complete(task)
                self.assertIn(task, self.manager.chats)
                self.assertEqual(self.service.store.grants(task), [])
                self.service.cancel_task(task)

    def test_runtime_model_reroute_and_early_notification_are_visible(self):
        for mode in ("model-rerouted", "model-rerouted-early"):
            with self.subTest(mode=mode):
                task = self.chat(mode)
                self.send("Synthetic turn")
                self.complete(task)
                self.assertEqual(
                    self.manager.model_info(task)["model"], "synthetic-rerouted-model"
                )
                self.assertIn(
                    "rerouted this turn", self.sidebar.state["activity"]["model"]
                )
                self.assertIn(
                    "synthetic-runtime-model", self.sidebar.state["activity"]["model"]
                )
                self.service.cancel_task(task)
                self.assertIn("last reported", self.sidebar.state["activity"]["model"])

    def test_foreign_model_reroute_cannot_change_the_selected_chat(self):
        task = self.chat("model-foreign-reroute")
        self.send("Owned turn")
        self.complete(task)
        self.assertEqual(
            self.manager.model_info(task)["model"], "synthetic-runtime-model"
        )
        self.assertNotIn("rerouted", self.sidebar.state["activity"]["model"])

    def test_invalid_reroute_marks_the_model_unknown_and_keeps_chat_connected(self):
        task = self.chat("model-invalid-reroute")
        self.send("Synthetic turn")
        self.complete(task)
        self.assertIsNone(self.manager.model_info(task))
        self.assertEqual(self.sidebar.state["activity"]["model"], "")
        self.assertIn(task, self.manager.chats)

    def test_new_turn_does_not_inherit_the_previous_turns_reroute(self):
        task = self.chat("model-rerouted-once")
        self.send("Rerouted turn")
        self.complete(task)
        self.assertIn(
            "synthetic-rerouted-model", self.sidebar.state["activity"]["model"]
        )
        self.send("Normal next turn")
        self.complete(task, 2)
        self.assertEqual(self.manager.model_info(task)["source"], "thread")
        self.assertIn(
            "synthetic-runtime-model", self.sidebar.state["activity"]["model"]
        )
        self.assertNotIn("rerouted", self.sidebar.state["activity"]["model"])

    def test_model_reports_remain_scoped_when_switching_conversations(self):
        first = self.chat("model-rerouted")
        self.send("First")
        self.complete(first)
        second = self.chat()
        self.assertNotIn(
            "synthetic-rerouted-model", self.sidebar.state["activity"]["model"]
        )
        self.sidebar.select_task(first)
        self.assertIn(
            "synthetic-rerouted-model", self.sidebar.state["activity"]["model"]
        )
        self.assertEqual(self.manager.model_info(second)["source"], "thread")
