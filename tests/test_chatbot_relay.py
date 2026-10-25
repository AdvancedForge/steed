"""Real task broker, permission, persistence and cancellation checks for JSON relay."""

import json
import sqlite3
import threading
import unittest
from concurrent.futures import Future
from unittest.mock import patch

import test_task_services as fixtures
from test_chatbot_protocol import call, response

from services.chatbot_protocol import RelayError
from services.chatbot_relay import ChatbotRelay


class ChatbotRelayTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    setUp = fixtures.TaskServiceTests.setUp
    tearDown = fixtures.TaskServiceTests.tearDown
    wait_for = fixtures.TaskServiceTests.wait_for

    def relay(self, guard=lambda: None):
        runner = ChatbotRelay(
            self.service, "Read a synthetic fixture", guard, parent=self.service
        )
        self.addCleanup(runner.stop)
        return runner

    def run_calls(self, runner, *calls):
        runner.execute(runner.review(response(*calls, exchange=runner.exchange_id)))
        self.wait_for(lambda: not runner.busy)
        return runner.results

    def test_discovery_is_unprivileged_and_results_form_next_message(self):
        runner = self.relay()
        first = json.loads(runner.outbound())
        self.assertIn("available_tools", first)
        self.assertEqual(first["goal"], "Read a synthetic fixture")
        self.assertTrue(first["new_user_messages"])
        results = self.run_calls(runner, call("steed_task_capabilities"))
        self.assertTrue(results[0]["ok"])
        next_message = json.loads(runner.outbound())
        self.assertNotEqual(next_message["exchange_id"], first["exchange_id"])
        self.assertEqual(next_message["results_for_exchange"], first["exchange_id"])
        self.assertEqual(next_message["results"], results)
        self.assertNotIn("available_tools", next_message)
        self.assertEqual(self.service.store.grants(runner.task_id), [])

    def test_export_watermark_requires_explicit_export_of_prepared_message(self):
        runner = self.relay()
        with self.assertRaises(RelayError):
            runner.exported(runner.exchange_id)
        runner.outbound()
        self.assertEqual(self.service.messages.read_sequence(runner.task_id), 0)
        runner.exported(runner.exchange_id)
        self.assertGreater(self.service.messages.read_sequence(runner.task_id), 0)
        self.service.send_message(runner.task_id, "New instruction")
        prepared = json.loads(runner.outbound())
        self.assertEqual(
            [m["text"] for m in prepared["new_user_messages"]], ["New instruction"]
        )
        runner.exported(runner.exchange_id)
        self.assertFalse(json.loads(runner.outbound())["new_user_messages"])

    def test_grant_request_never_self_approves(self):
        runner = self.relay()
        results = self.run_calls(
            runner,
            call(
                "steed_grant_request",
                {"scope": {"origins": [self.origin], "allow_private_network": True}},
            ),
        )
        self.assertTrue(results[0]["ok"])
        grant = self.service.store.grants(runner.task_id)[0]
        self.assertEqual(grant.status, "requested")
        results = self.run_calls(
            runner, call("steed_session_open", identity="open-before-approval")
        )
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["error"]["code"], "approval_required")
        self.service.approve_grant(runner.task_id, grant.grant_id)
        results = self.run_calls(
            runner, call("steed_session_open", identity="open-after-approval")
        )
        self.assertTrue(results[0]["ok"])

    def test_whole_batch_validated_before_any_effect(self):
        runner = self.relay()
        with patch.object(self.service.store, "record_event") as event:
            with self.assertRaises(RelayError):
                runner.review(
                    response(
                        call(),
                        call("steed_grant_approve", identity="bad"),
                        exchange=runner.exchange_id,
                    )
                )
            event.assert_not_called()
        self.assertFalse(runner.used_ids)

    def test_first_failure_prevents_later_dispatch_and_redacts_raw_exception(self):
        runner = self.relay()
        with patch.dict(
            self.service.broker._handlers,
            {
                "task.status": lambda **kw: (_ for _ in ()).throw(
                    RuntimeError("SECRET_EXCEPTION_BODY")
                ),
                "task.capabilities": lambda **kw: self.fail(
                    "Later call must not dispatch"
                ),
            },
        ):
            results = self.run_calls(
                runner, call(), call("steed_task_capabilities", identity="two")
            )
        self.assertNotIn("SECRET_EXCEPTION_BODY", json.dumps(results))
        self.assertEqual(results[1]["error"]["code"], "not_dispatched")
        with self.assertRaises(RelayError):
            runner.review(response(call(identity="two"), exchange=runner.exchange_id))

    def test_guard_rechecked_at_dispatch(self):
        allowed = [True]

        def guard():
            if not allowed[0]:
                raise ValueError("Destination changed")

        runner = self.relay(guard)
        with patch.dict(
            self.service.broker._handlers,
            {"task.status": lambda **kw: self.fail("Guard must block dispatch")},
        ):
            runner.execute(runner.review(response(call(), exchange=runner.exchange_id)))
            allowed[0] = False
            self.wait_for(lambda: runner.stopped)
        self.assertFalse(runner.results)

    def test_stop_before_dispatch_and_late_return(self):
        runner = self.relay()
        pending = Future()
        with patch.dict(
            self.service.broker._handlers, {"task.status": lambda **kw: pending}
        ):
            runner.execute(runner.review(response(call(), exchange=runner.exchange_id)))
            self.wait_for(
                lambda: runner._future is not None and runner._future.running()
            )
            runner.stop()
            pending.set_result({"secret": "late result"})
            self.app.processEvents()
        self.assertTrue(runner.stopped)
        self.assertFalse(runner.results)
        self.assertEqual(self.service.store.get(runner.task_id).status, "cancelled")

    def test_revoked_folder_access_clears_buffered_results(self):
        runner = self.relay()
        folder = self.service.library.create_folder("Synthetic")
        self.service.library.enable(folder, True)
        results = self.run_calls(runner, call("steed_library_folders"))
        self.assertTrue(results[0]["ok"])
        self.service.library.enable(folder, False)
        self.assertTrue(runner.stopped)
        self.assertFalse(runner.results)

    def test_journal_failure_before_dispatch_blocks_effects(self):
        runner = self.relay()
        proposal = runner.review(response(call(), exchange=runner.exchange_id))
        with patch.object(
            self.service.store,
            "record_event",
            side_effect=sqlite3.OperationalError("synthetic"),
        ):
            with self.assertRaises(sqlite3.Error):
                runner.execute(proposal)
        self.assertTrue(self.service.failure)
        self.assertTrue(runner.stopped)
        self.assertFalse(runner.results)

    def test_oversize_result_omitted_and_following_call_not_dispatched(self):
        runner = self.relay()
        with patch.dict(
            self.service.broker._handlers,
            {"task.status": lambda **kw: {"large": "x" * (700 * 1024)}},
        ):
            results = self.run_calls(runner, call(), call(identity="two"))
        self.assertTrue(results[0]["result_omitted"])
        self.assertEqual(results[1]["error"]["code"], "not_dispatched")
        self.assertLess(len(runner.outbound().encode()), 1024 * 1024)

    def test_final_answer_saved_in_task_conversation(self):
        runner = self.relay()
        runner.execute(
            runner.review(
                json.dumps(
                    {
                        "version": 1,
                        "exchange_id": runner.exchange_id,
                        "final": "Synthetic answer",
                    }
                )
            )
        )
        messages = self.service.messages.read(runner.task_id)["messages"]
        self.assertEqual(messages[-1]["text"], "Synthetic answer")
        self.assertEqual(messages[-1]["role"], "agent")
        self.assertFalse(runner.busy)
        self.assertFalse(self.service.store.grants(runner.task_id))

    def test_invalid_goal_does_not_create_a_task(self):
        for goal in ("", "x" * 8193, "\x01"):
            with self.subTest(goal=goal[:20]), self.assertRaises(RelayError):
                ChatbotRelay(self.service, goal, lambda: None)
        self.assertFalse(self.service.store.list_tasks())

    def test_stop_before_queued_execution_blocks_effect(self):
        runner = self.relay()
        with patch.dict(
            self.service.broker._handlers,
            {"task.status": lambda **kw: self.fail("Cancelled call dispatched")},
        ):
            runner.execute(runner.review(response(call(), exchange=runner.exchange_id)))
            runner.stop()
            self.app.processEvents()
        self.assertFalse(runner.results)

    def test_guard_runs_after_enqueue_before_registered_handler(self):
        valid = [True]

        def guard():
            if not valid[0]:
                raise ValueError("Source changed after enqueue")

        runner = self.relay(guard)
        original = self.service.broker.submit_guarded

        def enqueue(*args, **kwargs):
            future = original(*args, **kwargs)
            valid[0] = False
            return future

        with (
            patch.object(self.service.broker, "submit_guarded", side_effect=enqueue),
            patch.dict(
                self.service.broker._handlers,
                {"task.status": lambda **kw: self.fail("Stale queued call dispatched")},
            ),
        ):
            runner.execute(runner.review(response(call(), exchange=runner.exchange_id)))
            self.wait_for(lambda: runner.stopped)
        self.assertFalse(runner.results)

    def test_worker_thread_return_is_consumed_on_qt_thread(self):
        runner = self.relay()
        pending = Future()
        with patch.dict(
            self.service.broker._handlers, {"task.status": lambda **kw: pending}
        ):
            runner.execute(runner.review(response(call(), exchange=runner.exchange_id)))
            self.wait_for(
                lambda: runner._future is not None and runner._future.running()
            )
            worker = threading.Thread(
                target=lambda: pending.set_result({"synthetic": True})
            )
            worker.start()
            worker.join()
            self.wait_for(lambda: not runner.busy)
        self.assertEqual(runner.results[0]["result"], {"synthetic": True})

    def test_journal_failure_after_effect_hides_unrecorded_result(self):
        runner = self.relay()
        original = self.service.store.record_event

        def record(task_id, kind, data):
            if kind == "relay.call.returned":
                raise sqlite3.OperationalError("Synthetic receipt failure")
            return original(task_id, kind, data)

        with patch.object(self.service.store, "record_event", side_effect=record):
            runner.execute(runner.review(response(call(), exchange=runner.exchange_id)))
            self.wait_for(lambda: runner.stopped)
        self.assertTrue(self.service.failure)
        self.assertFalse(runner.results)

    def test_timeout_and_exchange_limit_do_not_retry(self):
        runner = self.relay()
        runner.exchanges = 32
        with self.assertRaises(RelayError):
            runner.review(response(call(), exchange=runner.exchange_id))
        runner.deadline.timeout.emit()
        self.assertTrue(runner.stopped)
        self.assertIn("timed out", runner.status)

    def test_validation_feedback_is_structured_without_dispatch(self):
        runner = self.relay()
        nonce = runner.exchange_id
        with self.assertRaises(RelayError):
            runner.review("Unstructured chatbot response")
        message = json.loads(runner.outbound())
        self.assertEqual(message["response_feedback"]["code"], "invalid_json")
        self.assertFalse(message["response_feedback"]["requests_dispatched"])
        self.assertEqual(message["exchange_id"], nonce)
        self.assertFalse(runner.used_ids)
        for example in message["response_examples"]:
            runner.review(json.dumps(example))
        self.assertIsNone(runner.response_feedback)

    def test_invalid_scope_keeps_specific_code_field_and_guidance(self):
        runner = self.relay()
        results = self.run_calls(
            runner,
            call(
                "steed_grant_request",
                {"scope": {"origins": ["https://example.com/invalid-path"]}},
            ),
        )
        error = results[0]["error"]
        self.assertEqual(error["code"], "invalid_scope")
        self.assertEqual(error["field"], "origins")
        self.assertIn("exact HTTP(S) origins", error["message"])
