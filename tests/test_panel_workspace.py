"""Owned sealed workspace sources, bounded image previews and authority boundaries."""

import base64
import hashlib
import json
import unittest

import test_task_profiles as profiles
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage

from browser_window import BrowserWindow
from data_panel_format import parse_panel
from services.panel_images import decode_image
from services.task_policy import TaskPermissionError
from services.task_workspace import TaskWorkspace


def png(width=80, height=50, color=0xFFEEDD):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(color)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class WorkspacePanelTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown
    wait_for = profiles.TaskProfileTests.wait_for
    command = profiles.TaskProfileTests.command
    request = profiles.TaskProfileTests.request

    def task(self, actions=("workspace",), operations=1000):
        task = self.command("task.create", objective="Show generated files")
        grant = self.request(
            task.task_id, actions=list(actions), max_operations=operations
        )
        self.service.approve_grant(task.task_id, grant.grant_id)
        return task

    def file(self, task, data, name="chart.png", media_type="image/png", sealed=True):
        # Only the synthetic fixture creates bytes directly, preserving budgets
        # so operation-count assertions measure the public panel command.
        workspace = TaskWorkspace(self.service.store)
        item = workspace.begin(
            task.task_id, name, media_type, len(data), hashlib.sha256(data).hexdigest()
        )
        for offset in range(0, len(data), 32768):
            workspace.write(
                task.task_id,
                item.file_id,
                offset,
                base64.b64encode(data[offset : offset + 32768]).decode(),
            )
        return workspace.seal(task.task_id, item.file_id) if sealed else item

    def source(self, item, kind="image", version=3):
        return json.dumps(
            {
                "version": version,
                "name": "Generated result",
                "widgets": [
                    {
                        "id": "output",
                        "kind": kind,
                        "title": "Result",
                        "file_id": item.file_id,
                    }
                ],
            }
        )

    def propose(self, task, item, kind="image", request="one"):
        return self.command(
            "panel.propose",
            task_id=task.task_id,
            source=self.source(item, kind),
            request_id=request,
        )

    def approve(self, task, receipt):
        key = self.service.data_panels.profile_key("work", create=True)
        return self.service.data_panels.resolve(
            task.task_id,
            receipt.request_id,
            approve=True,
            profile="work",
            profile_key=key,
        )

    def test_versions_keep_old_grammar_and_source_type_cannot_be_switched(self):
        task = self.task()
        item = self.file(task, b"Text", name="note.txt", media_type="text/plain")
        for version in (1, 2):
            with self.assertRaises(ValueError):
                parse_panel(self.source(item, "text", version))
        receipt = self.propose(task, item, "text")
        self.approve(task, receipt)
        with self.assertRaises(ValueError):
            self.command(
                "panel.publish",
                task_id=task.task_id,
                request_id="one",
                update_id="wrong-type",
                expected_revision=0,
                bindings=[{"id": "output", "artifact_id": item.file_id}],
            )
        self.assertEqual(self.service.data_panels.get(task.task_id, "one").revision, 0)
        with self.assertRaises(ValueError):
            self.command(
                "panel.publish",
                task_id=task.task_id,
                request_id="one",
                update_id="invalid-id",
                expected_revision=0,
                bindings=[{"id": [], "file_id": item.file_id}],
            )

    def test_workspace_authority_is_required_without_artifact_authority(self):
        task = self.task()
        item = self.file(task, png())
        receipt = self.propose(task, item)
        self.assertEqual(receipt.state, "awaiting_approval")
        self.assertEqual(self.service._grants[task.task_id][1].operations, 1)
        other = self.task(actions=("artifact",))
        other_file = self.file(other, png())
        with self.assertRaises(TaskPermissionError):
            self.propose(other, other_file)
        with self.assertRaises(KeyError):
            self.propose(task, other_file, request="foreign")

    def test_mixed_sources_consume_one_operation_and_preserve_retry_hash(self):
        task = self.task(actions=("workspace", "artifact"), operations=1)
        file = self.file(task, png())
        artifact = self.service.store.store_artifact(task.task_id, "Summary", "Summary")
        document = json.loads(self.source(file))
        document["widgets"].append(
            {
                "id": "summary",
                "kind": "text",
                "title": "Summary",
                "artifact_id": artifact.artifact_id,
            }
        )
        source = json.dumps(document)
        receipt = self.command(
            "panel.propose", task_id=task.task_id, request_id="mixed", source=source
        )
        self.assertEqual(self.service._grants[task.task_id][1].operations, 1)
        self.assertEqual(
            self.command(
                "panel.propose", task_id=task.task_id, request_id="mixed", source=source
            ),
            receipt,
        )

    def test_text_list_table_utf8_size_and_sealed_checks(self):
        task = self.task()
        for kind, body, expected in (
            ("text", "<b>Literal</b>", "<b>Literal</b>"),
            ("list", '["One", "Two"]', ("One", "Two")),
            (
                "table",
                '{"columns":["Name"],"rows":[["One"]]}',
                {"columns": ("Name",), "rows": (("One",),)},
            ),
        ):
            item = self.file(
                task, body.encode(), name=kind + ".json", media_type="application/json"
            )
            receipt = self.propose(task, item, kind, request=kind)
            self.assertEqual(
                self.service.data_panels.view(task.task_id, receipt.request_id)[0][
                    "content"
                ],
                expected,
            )
            self.service.data_panels.resolve(
                task.task_id, receipt.request_id, approve=False
            )
        for name, body, sealed in (
            ("invalid.txt", b"\xff", True),
            ("too-big.txt", b"x" * 16385, True),
            ("draft.txt", b"Draft", False),
        ):
            item = self.file(
                task, body, name=name, media_type="text/plain", sealed=sealed
            )
            with self.assertRaises(ValueError):
                self.propose(task, item, "text", request=name.replace(".", "-"))

    def test_images_reject_mismatch_external_formats_corruption_and_pixel_bombs(self):
        for data, media in (
            (png(), "image/jpeg"),
            (b"<svg><image href='https://example.org'/></svg>", "image/svg+xml"),
            (b"not an image", "image/png"),
            (png(2050, 2050), "image/png"),
        ):
            with self.subTest(media=media), self.assertRaises(ValueError):
                decode_image(data, media)
        self.assertEqual(
            decode_image(png(), "image/png").size(), QImage.fromData(png()).size()
        )

    def test_image_cache_is_bounded_and_deleted_or_tampered_sources_never_fall_back(
        self,
    ):
        task = self.task()
        first = None
        for index in range(10):
            item = self.file(
                task, png(color=0xFF0000 + index), name=f"image-{index}.png"
            )
            receipt = self.propose(task, item, request="image-" + str(index))
            self.service.data_panels.resolve(
                task.task_id, receipt.request_id, approve=False
            )
            first = first or (item, receipt)
        self.assertEqual(len(self.service.data_panels._images), 8)
        item, receipt = first
        with self.service.store._db:
            self.service.store._db.execute(
                "UPDATE workspace_files SET data=? WHERE file_id=?",
                (png(color=0x0000FF), item.file_id),
            )
        with self.assertRaises(ValueError):
            self.service.data_panels.view(task.task_id, receipt.request_id)
        TaskWorkspace(self.service.store).delete(task.task_id, item.file_id)
        with self.assertRaises(ValueError):
            self.service.data_panels.view(task.task_id, receipt.request_id)

    def test_publication_retries_are_stable_and_retained_images_freeze_at_task_end(
        self,
    ):
        task = self.task()
        initial = self.file(task, png())
        receipt = self.propose(task, initial)
        self.approve(task, receipt)
        next_file = self.file(task, png(color=0x224466), name="next.png")
        arguments = dict(
            task_id=task.task_id,
            request_id="one",
            update_id="refresh",
            expected_revision=0,
            bindings=[{"id": "output", "file_id": next_file.file_id}],
        )
        update = self.command("panel.publish", **arguments)
        self.assertEqual(self.command("panel.publish", **arguments), update)
        self.assertEqual(
            self.service.data_panels.view(task.task_id, "one")[0]["file_id"],
            next_file.file_id,
        )
        self.command("task.finish", task_id=task.task_id)
        self.assertFalse(
            self.service.data_panels.view(task.task_id, "one")[0]["content"].isNull()
        )
        with self.assertRaises(TaskPermissionError):
            self.command(
                "panel.publish",
                **{**arguments, "update_id": "after-end", "expected_revision": 1},
            )

    def test_profile_switch_scrubs_pixels_and_source_records(self):
        task = self.task()
        item = self.file(task, png())
        receipt = self.propose(task, item)
        self.approve(task, receipt)
        window = BrowserWindow("work")
        self.windows.append(window)
        window._toggle_data_panels(True)
        dock = window.data_panels_dock
        dock.refresh()
        content = dock.scroll.widget()
        display = content.displays["output"]
        self.assertFalse(display._image.isNull())
        window._switch_to_profile("search")
        self.assertTrue(display._image.isNull())
        self.assertEqual(content._widgets, {})
        self.assertIsNone(dock.scroll.widget())
