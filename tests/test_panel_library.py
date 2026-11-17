"""Reusable exact-file panel access, revocation, profile isolation and snapshots."""

import json
import sqlite3
import unittest
from copy import deepcopy
from unittest.mock import patch

import test_data_panels as panels
from PyQt6 import sip

from browser.data_panel_format import parse_panel
from browser.layout_format import export_layout
from browser.layout_preferences import preferences_from_config
from services.task_policy import TaskPermissionError
from services.task_service import TaskService
from ui.data_panels import DataPanelReviewDialog
from ui.prompts import MessageBox as QMessageBox


class LibraryPanelTests(unittest.TestCase):
    setUpClass = classmethod(panels.DataPanelTests.setUpClass.__func__)
    tearDownClass = classmethod(panels.DataPanelTests.tearDownClass.__func__)
    setUp = panels.DataPanelTests.setUp
    tearDown = panels.DataPanelTests.tearDown
    command = panels.DataPanelTests.command
    wait_for = panels.DataPanelTests.wait_for
    request = panels.DataPanelTests.request
    task = panels.DataPanelTests.task
    window = panels.DataPanelTests.window
    artifact = panels.DataPanelTests.artifact

    def source(self, *, kind="text", extra=()):
        return json.dumps(
            {
                "version": 4,
                "name": "Linked daily notes",
                "widgets": [
                    {
                        "id": "notes",
                        "kind": kind,
                        "title": "Daily notes",
                        "library": {"folder_id": self.folder, "path": "notes.txt"},
                    },
                    *extra,
                ],
            }
        )

    def prepare(
        self, *, allow=True, content=b"SYNTHETIC_LINKED_NOTES", kind="text", extra=()
    ):
        self.folder = self.service.library.create_folder(
            f"Fixture folder {len(self.service.library.folders()) + 1}"
        )
        self.service.library.import_files(self.folder, [("notes.txt", content)])
        self.service.library.enable(self.folder, True)
        self.owner = self.task(grant=False)
        self.receipt = self.command(
            "panel.propose",
            task_id=self.owner.task_id,
            request_id="linked",
            source=self.source(kind=kind, extra=extra),
        )
        self.key = self.service.data_panels.profile_key("work", create=True)
        self.service.data_panels.resolve(
            self.owner.task_id,
            "linked",
            approve=True,
            profile="work",
            profile_key=self.key,
            library_access=allow,
        )

    def view(self):
        return self.service.data_panels.view(self.owner.task_id, "linked")[0]

    def access(self, enabled, key=None):
        self.service.data_panels.set_source_access(
            self.owner.task_id, "linked", profile_key=key or self.key, enabled=enabled
        )

    def test_old_grammars_and_exact_source_fields(self):
        self.prepare()
        data = json.loads(self.source())
        for change in (
            lambda d: d.update(version=3),
            lambda d: d["widgets"][0].update(kind="image"),
            lambda d: d["widgets"][0].update(file_id="other"),
            lambda d: d["widgets"][0]["library"].update(path="../private"),
            lambda d: d["widgets"][0]["library"].update(path="/tmp/private"),
            lambda d: d["widgets"][0]["library"].update(revision=1),
            lambda d: d["widgets"][0]["library"].update(folder_id="../../private"),
        ):
            altered = deepcopy(data)
            change(altered)
            with self.assertRaises(ValueError):
                parse_panel(json.dumps(altered))

    def test_linked_lists_and_tables_refresh_and_clear_native_rows(self):
        for kind, content in (
            ("list", b'["First","Second"]'),
            ("table", b'{"columns":["Name"],"rows":[["First"],["Second"]]}'),
        ):
            self.prepare(kind=kind, content=content)
            window = self.window()
            window._toggle_data_panels(True)
            dock = window.data_panels_dock
            index = next(
                i
                for i in range(dock.selector.count())
                if dock.selector.itemData(i)[0] == self.owner.task_id
            )
            dock.selector.setCurrentIndex(index)
            display = dock.scroll.widget().displays["notes"]
            self.assertEqual(
                display.count() if kind == "list" else display.rowCount(), 2
            )
            self.service.library.enable(self.folder, False)
            self.assertEqual(
                display.count() if kind == "list" else display.rowCount(), 0
            )
            self.service.library.enable(self.folder, True)
            self.assertEqual(
                display.count() if kind == "list" else display.rowCount(), 2
            )

    def test_failed_source_access_write_leaves_the_panel_unapproved(self):
        self.folder = self.service.library.create_folder("Fixture folder")
        self.service.library.import_files(self.folder, [("notes.txt", b"NOTES")])
        self.service.library.enable(self.folder, True)
        self.owner = self.task(grant=False)
        self.command(
            "panel.propose",
            task_id=self.owner.task_id,
            request_id="linked",
            source=self.source(),
        )
        key = self.service.data_panels.profile_key("work", create=True)
        append = self.service.store._append

        def failing(task_id, kind, payload):
            if kind == "panel.source_access":
                raise sqlite3.OperationalError("synthetic journal failure")
            return append(task_id, kind, payload)

        with (
            patch.object(self.service.store, "_append", failing),
            self.assertRaises(sqlite3.OperationalError),
        ):
            self.service.data_panels.resolve(
                self.owner.task_id,
                "linked",
                approve=True,
                profile="work",
                profile_key=key,
                library_access=True,
            )
        row = self.service.data_panels._row(self.owner.task_id, "linked")
        self.assertEqual(row["state"], "awaiting_approval")
        self.assertNotEqual(row["profile_key"], key)

    def test_panel_review_is_separate_from_reusable_source_permission(self):
        self.prepare(allow=False)
        self.assertFalse(self.view()["available"])
        self.assertEqual(self.view()["content"], "")
        self.assertEqual(self.service.store.grants(self.owner.task_id), [])
        self.access(True)
        self.assertEqual(self.view()["content"], "SYNTHETIC_LINKED_NOTES")
        self.access(False)
        self.assertFalse(self.view()["available"])
        self.assertEqual(self.view()["content"], "")
        with self.assertRaises(ValueError):
            self.access(True, key="another-profile")
        self.assertEqual(self.service.data_panels.for_profile("global"), [])

    def test_source_permission_and_live_updates_survive_task_end_and_restart(self):
        self.prepare()
        self.command("task.finish", task_id=self.owner.task_id, status="completed")
        with self.service.library.db:
            self.service.library.put(
                self.folder, "notes.txt", "file", b"LATEST_NOTES", 1
            )
        self.assertEqual(self.view()["source_revision"], 2)
        self.assertEqual(self.view()["content"], "LATEST_NOTES")
        self.service.close()
        restarted = TaskService(
            self.config.config_dir / "tasks.sqlite", profiles=self.registry
        )
        try:
            self.assertTrue(
                restarted.data_panels.source_access(self.owner.task_id, "linked")
            )
            self.assertEqual(
                restarted.data_panels.view(self.owner.task_id, "linked")[0]["content"],
                "LATEST_NOTES",
            )
            restarted.library.enable(self.folder, False)
            self.assertEqual(
                restarted.data_panels.view(self.owner.task_id, "linked")[0]["content"],
                "",
            )
        finally:
            restarted.close()

    def test_folder_revocation_scrubs_open_widget_and_preview_immediately(self):
        self.prepare()
        pending = self.command(
            "panel.propose",
            task_id=self.owner.task_id,
            request_id="pending",
            source=self.source(),
        )
        window = self.window()
        window._toggle_data_panels(True)
        dock = window.data_panels_dock
        display = dock.scroll.widget().displays["notes"]
        dialog = DataPanelReviewDialog(self.service, pending, window)
        self.addCleanup(lambda: None if sip.isdeleted(dialog) else dialog.close())
        preview = dialog.preview.widget().displays["notes"]
        self.assertIn("SYNTHETIC_LINKED_NOTES", display.toPlainText())
        self.assertIn("SYNTHETIC_LINKED_NOTES", preview.toPlainText())
        self.service.library.enable(self.folder, False)
        # Deliberately no processEvents: revocation must erase native copies now.
        self.assertEqual(display.toPlainText(), "")
        self.assertEqual(preview.toPlainText(), "")
        self.assertNotIn("SYNTHETIC_LINKED_NOTES", str(dock.scroll.widget()._widgets))
        self.service.library.enable(self.folder, True)
        self.assertEqual(display.toPlainText(), "SYNTHETIC_LINKED_NOTES")
        self.assertEqual(preview.toPlainText(), "SYNTHETIC_LINKED_NOTES")
        dialog.show()
        self.app.processEvents()
        dialog.grab().save("/tmp/steed-library-panel-review.png")
        window.grab().save("/tmp/steed-library-panel-window.png")

    def test_disable_panel_access_erases_data_without_disabling_folder(self):
        self.prepare()
        window = self.window()
        window._toggle_data_panels(True)
        dock = window.data_panels_dock
        display = dock.scroll.widget().displays["notes"]
        dock.change_source_access()
        self.assertEqual(display.toPlainText(), "")
        self.assertTrue(self.service.library.folder(self.folder)["enabled"])
        self.assertIn("disabled", dock.status.text())

    def test_reallow_popup_respects_decline_and_profile_changes(self):
        self.prepare(allow=False)
        window = self.window()
        window._toggle_data_panels(True)
        dock = window.data_panels_dock
        with patch.object(
            QMessageBox, "exec", return_value=QMessageBox.StandardButton.No
        ):
            dock.change_source_access()
        self.assertFalse(self.view()["available"])

        def switched():
            window._switch_to_profile("global")
            return QMessageBox.StandardButton.Yes

        with patch.object(QMessageBox, "exec", side_effect=switched):
            dock.change_source_access()
        self.assertFalse(self.view()["available"])
        window._switch_to_profile("work")
        with patch.object(
            QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes
        ):
            dock.change_source_access()
        self.assertEqual(self.view()["content"], "SYNTHETIC_LINKED_NOTES")

    def test_permission_storage_failure_clears_native_data_and_refuses_reads(self):
        self.prepare()
        window = self.window()
        window._toggle_data_panels(True)
        dock = window.data_panels_dock
        display = dock.scroll.widget().displays["notes"]
        with patch.object(
            self.service.store,
            "_append",
            side_effect=sqlite3.OperationalError("synthetic"),
        ):
            with self.assertRaises(sqlite3.Error):
                self.access(False)
        self.assertTrue(self.service.failure)
        self.assertEqual(display.toPlainText(), "")
        self.assertIsNone(dock.scroll.widget())
        with self.assertRaises(RuntimeError):
            self.view()

    def test_review_requires_explicit_checkbox_and_agents_cannot_grant_or_retarget(
        self,
    ):
        self.prepare(allow=False)
        pending = self.command(
            "panel.propose",
            task_id=self.owner.task_id,
            request_id="pending",
            source=self.source(),
        )
        dialog = DataPanelReviewDialog(self.service, pending)
        self.addCleanup(lambda: None if sip.isdeleted(dialog) else dialog.close())
        self.assertFalse(dialog.linked_access.isChecked())
        dialog.profiles.setCurrentText("work")
        dialog.linked_access.setChecked(True)
        dialog.approve()
        self.assertTrue(
            self.service.data_panels.source_access(self.owner.task_id, "pending")
        )
        future = self.service.client(self.owner.task_id).submit(
            "panel.source_access",
            request_id="linked",
            profile_key=self.key,
            enabled=True,
        )
        self.app.processEvents()
        with self.assertRaises(TaskPermissionError):
            future.result()
        with self.assertRaises(ValueError):
            self.command(
                "panel.publish",
                task_id=self.owner.task_id,
                request_id="linked",
                update_id="retarget",
                expected_revision=0,
                bindings=[
                    {
                        "id": "notes",
                        "library": {"folder_id": self.folder, "path": "other"},
                    }
                ],
            )

    def test_deleted_corrupt_large_and_malformed_sources_never_retain_content(self):
        for content, kind in (
            (b"\xff", "text"),
            (b"x" * 16385, "text"),
            (b"invalid JSON", "list"),
            (b"invalid JSON", "table"),
        ):
            self.prepare()
            with self.service.library.db:
                self.service.library.put(self.folder, "notes.txt", "file", content, 1)
            if kind != "text":
                widget = dict(self.view())
                widget["kind"] = kind
                with self.assertRaises(ValueError):
                    self.service.data_panels._library_content(widget)
            else:
                self.assertFalse(self.view()["available"])
                self.assertEqual(self.view()["content"], "")
            # Allow a fresh fixture name/receipt within this same temporary profile.
            self.service.data_panels.remove(self.owner.task_id, "linked")
            self.service.library.db.execute("UPDATE folders SET name=id")
            self.service.library.db.commit()
        self.prepare()
        self.service.library.remove(self.folder, "notes.txt", 1)
        self.assertFalse(self.view()["available"])
        self.service.library.restore(self.folder, "notes.txt")
        self.assertTrue(self.view()["available"])
        self.service.library.db.execute(
            "UPDATE entries SET data=? WHERE folder=? AND path=?",
            (b"TAMPERED", self.folder, "notes.txt"),
        )
        self.service.library.db.commit()
        self.assertFalse(self.view()["available"])

    def test_removed_panel_and_recreated_profile_cannot_reuse_access(self):
        self.prepare()
        self.manager.rename_profile("work", "renamed")
        self.assertTrue(
            self.service.data_panels.source_access(self.owner.task_id, "linked")
        )
        self.manager.delete_profile("renamed")
        self.manager.create_profile("renamed")
        self.assertFalse(
            self.service.data_panels.source_access(self.owner.task_id, "linked")
        )
        self.assertFalse(self.view()["available"])
        with self.assertRaises(ValueError):
            self.access(True)
        self.service.data_panels.remove(self.owner.task_id, "linked")
        self.assertFalse(
            self.service.data_panels.source_access(self.owner.task_id, "linked")
        )

    def test_linked_paths_and_contents_stay_out_of_receipts_and_appearance_exports(
        self,
    ):
        self.prepare()
        events = json.dumps(
            [e.payload for e in self.service.store.events(self.owner.task_id)]
        )
        appearance = export_layout(preferences_from_config(self.config))
        for private in ("SYNTHETIC_LINKED_NOTES", "notes.txt", self.folder):
            self.assertNotIn(private, events)
            self.assertNotIn(private, str(appearance))

    def test_saved_reports_remain_when_a_linked_source_is_revoked(self):
        # A saved report has its own task/authority; linking does not alter retention.
        saved_owner = self.task()
        saved = self.artifact(saved_owner, "SAVED_REPORT")
        self.prepare(extra=())
        self.command(
            "panel.propose",
            task_id=saved_owner.task_id,
            request_id="saved",
            source=panels.DataPanelTests.source(self, saved),
        )
        self.service.data_panels.resolve(
            saved_owner.task_id, "saved", approve=True, profile="work"
        )
        self.service.library.enable(self.folder, False)
        self.assertEqual(
            self.service.data_panels.view(saved_owner.task_id, "saved")[1]["content"],
            "SAVED_REPORT",
        )
