"""User activity, scoped download progress/cancellation and persisted receipts."""

import unittest

import test_task_downloads as fixtures

from services.task_policy import TaskPermissionError
from ui.task_dialog import TaskDialog


class TaskActivityTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskDownloadTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskDownloadTests.tearDownClass.__func__)
    setUp = fixtures.TaskDownloadTests.setUp
    tearDown = fixtures.TaskDownloadTests.tearDown
    command = fixtures.TaskDownloadTests.command
    load = fixtures.TaskDownloadTests.load
    inspect = fixtures.TaskDownloadTests.inspect
    wait_for = fixtures.TaskDownloadTests.wait_for
    opened = fixtures.TaskDownloadTests.opened
    start = fixtures.TaskDownloadTests.start
    finished = fixtures.TaskDownloadTests.finished

    def dialog(self):
        dialog = TaskDialog(self.service)
        self.addCleanup(dialog.close)
        return dialog

    def test_receipt_survives_session_completion_without_showing_payload_values(self):
        task, _, session, _ = self.opened()
        result = self.finished(session, self.start(task, session))
        self.command("task.finish", task_id=task)
        self.service.store.record_event(
            task, "page.action_finished", {"text": "SYNTHETIC_PRIVATE_VALUE"}
        )
        dialog = self.dialog()
        self.assertEqual(dialog.downloads.rowCount(), 1)
        self.assertEqual(dialog.downloads.item(0, 0).text(), str(self.target))
        self.assertEqual(dialog.downloads.item(0, 1).text(), "completed")
        self.assertIn(f"{result.received_bytes:,}", dialog.downloads.item(0, 2).text())
        self.assertIn("Download completed", dialog.activity.toPlainText())
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", dialog.activity.toPlainText())
        dialog.downloads.selectRow(0)
        self.assertFalse(dialog.cancel_download_button.isEnabled())
        dialog.close()
        self.assertFalse(dialog.timer.isActive())

    def test_user_can_cancel_selected_stream_without_cancelling_the_task(self):
        task, _, session, _ = self.opened(link="/download-slow", size=1024 * 1024)
        type(self).page_fixtures["/download-slow"] = b"d" * (512 * 1024)
        started = self.start(task, session)
        transfer = self.service.backend._sessions[session].downloads[
            started.download_id
        ]
        self.wait_for(lambda: transfer.received > 0)
        dialog = self.dialog()
        dialog.downloads.selectRow(0)
        self.assertTrue(dialog.cancel_download_button.isEnabled())
        dialog.cancel_download_button.click()
        self.assertEqual(dialog.downloads.item(0, 1).text(), "cancelled")
        self.assertFalse(dialog.cancel_download_button.isEnabled())
        self.assertEqual(self.service.store.get(task).status, "running")
        self.assertFalse(self.target.exists())

    def test_failed_destination_is_terminal_and_task_switches_isolate_rows(self):
        task, _, session, _ = self.opened()
        self.target.write_bytes(b"existing")
        with self.assertRaises(TaskPermissionError):
            self.start(task, session)
        dialog = self.dialog()
        self.assertEqual(dialog.downloads.item(0, 1).text(), "failed")
        self.assertEqual(dialog.downloads.item(0, 3).text(), "destination exists")
        other = self.command("task.create", objective="Separate task")
        row = next(
            i for i, (t, _) in enumerate(dialog._rows) if t.task_id == other.task_id
        )
        dialog.table.selectRow(row)
        self.assertEqual(dialog.downloads.rowCount(), 0)
        self.assertNotIn("Download", dialog.activity.toPlainText())
