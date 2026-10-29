"""conversation.wait wakes an idle client on the user's next message."""

import time
import unittest

import test_task_services as fixtures
from PyQt6.QtCore import QTimer


class ConversationWaitTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskServiceTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskServiceTests.tearDownClass.__func__)
    setUp = fixtures.TaskServiceTests.setUp
    tearDown = fixtures.TaskServiceTests.tearDown
    wait_for = fixtures.TaskServiceTests.wait_for
    command = fixtures.TaskServiceTests.command

    def task(self):
        task_id = self.command("task.create", objective="Chat").task_id
        self.service.external_connection(task_id, True)
        return task_id

    def wait(self, task_id, **arguments):
        return self.service.broker.submit(
            "conversation.wait", task_id=task_id, **arguments
        )

    def test_returns_when_the_user_sends_a_message(self):
        task_id = self.task()
        started = time.monotonic()
        pending = self.wait(task_id, timeout_ms=10000)
        QTimer.singleShot(300, lambda: self.service.send_message(task_id, "Hello"))
        self.wait_for(pending.done)
        page = pending.result()
        self.assertEqual(page["waited"], "message")
        self.assertEqual([m["text"] for m in page["messages"]], ["Hello"])
        self.assertLess(time.monotonic() - started, 5)
        # Waiting from the returned cursor blocks again until something new.
        later = self.wait(task_id, after=page["cursor"], timeout_ms=200)
        self.wait_for(later.done)
        self.assertEqual(later.result()["waited"], "timed_out")
        self.assertEqual(later.result()["messages"], [])

    def test_unread_messages_return_at_once_and_agent_replies_do_not_wake(self):
        task_id = self.task()
        self.service.send_message(task_id, "Already here")
        immediate = self.wait(task_id, timeout_ms=10000)
        self.wait_for(immediate.done)
        self.assertEqual(immediate.result()["waited"], "message")
        cursor = immediate.result()["cursor"]
        pending = self.wait(task_id, after=cursor, timeout_ms=600)
        self.command(
            "conversation.reply",
            task_id=task_id,
            message_id="reply-1",
            text="Working on it",
        )
        self.wait_for(pending.done)
        self.assertEqual(pending.result()["waited"], "timed_out")

    def test_one_wait_per_task_bounded_timeout_and_task_end(self):
        task_id = self.task()
        pending = self.wait(task_id, timeout_ms=10000)
        second = self.wait(task_id, timeout_ms=100)
        self.wait_for(second.done)
        self.assertIsInstance(second.exception(), ValueError)
        too_long = self.wait(self.task(), timeout_ms=60000)
        self.wait_for(too_long.done)
        self.assertIsNotNone(too_long.exception())
        self.service.cancel_task(task_id)
        self.wait_for(pending.done)
        self.assertEqual(pending.result()["waited"], "task_ended")


if __name__ == "__main__":
    unittest.main()
