"""Visual permission, real canvas evidence, image bounds and revocation."""

import base64
import json
import unittest
from unittest.mock import patch

import test_live_tabs as fixtures
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage

from agent_mcp import tool_result
from services.page_capture import image_payload
from services.page_inspector import PageInspectionError
from services.task_policy import LiveGrant, TaskPermissionError, TaskScope


class PageCaptureTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.LiveTabsTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LiveTabsTests.tearDownClass.__func__)
    setUp = fixtures.LiveTabsTests.setUp
    tearDown = fixtures.LiveTabsTests.tearDown
    wait_for = fixtures.LiveTabsTests.wait_for
    command = fixtures.LiveTabsTests.command
    window = fixtures.LiveTabsTests.window
    share = fixtures.LiveTabsTests.share
    task = fixtures.LiveTabsTests.task
    value = fixtures.LiveTabsTests.value

    def capture(self, task, session):
        return self.command("page.capture", task_id=task, session_id=session)

    def test_native_capture_contains_pixels_preserves_viewport_and_omits_journal_image(
        self,
    ):
        window, view = self.window()
        self.value(
            view.page(),
            "document.body.style.background='#ff1122'; document.body.style.margin='0'; true",
        )
        task, _, session = self.share(window)
        canvas = self.value(
            view.page(),
            """(() => {
            const canvas = document.querySelector('canvas'), ctx = canvas.getContext('2d');
            ctx.fillStyle = '#00ee11'; ctx.fillRect(0, 0, canvas.width, canvas.height);
            const rect = canvas.getBoundingClientRect(); return {x:rect.x,y:rect.y};
        })()""",
        )
        self.wait_for(lambda: self.value(view.page(), "innerHeight") == view.height())
        before = self.value(view.page(), "[innerWidth,innerHeight,scrollX,scrollY]")
        evidence = self.capture(task, session)
        self.assertEqual(evidence["source"], "native_viewport")
        self.assertEqual(evidence["image"]["mime_type"], "image/jpeg")
        decoded = base64.b64decode(evidence["image"]["data"])
        self.assertLessEqual(len(decoded), 256000)
        image = QImage.fromData(decoded)
        self.assertFalse(image.isNull())
        self.assertLessEqual(image.width(), 1280)
        self.assertLessEqual(image.height(), 960)
        pixel = image.pixelColor(1, image.height() - 2)
        self.assertGreater(pixel.red(), 220)
        self.assertLess(pixel.green(), 50)
        self.assertLess(pixel.blue(), 70)
        canvas_pixel = image.pixelColor(int(canvas["x"]) + 20, int(canvas["y"]) + 20)
        self.assertGreater(canvas_pixel.green(), 200)
        self.assertLess(canvas_pixel.red(), 70)
        self.assertEqual(
            before, self.value(view.page(), "[innerWidth,innerHeight,scrollX,scrollY]")
        )
        self.assertNotIn(
            evidence["image"]["data"], repr(self.service.store.events(task))
        )
        self.assertTrue(
            any(
                event.kind == "page.captured"
                for event in self.service.store.events(task)
            )
        )
        result = tool_result({"result": evidence}, images=True)
        self.assertEqual(result.content[1].type, "image")
        self.assertNotIn("image", result.structuredContent["result"])
        self.assertNotIn(evidence["image"]["data"], result.content[0].text)
        window._take_back_current_tab()
        self.assertIsNone(view.page().devToolsPage())

    def test_text_read_grant_cannot_capture_without_visual_approval(self):
        window, _ = self.window()
        task, _, session = self.share(window)
        existing = self.service._grants[task][1]
        payload = existing.scope.payload()
        payload["actions"] = [
            action for action in payload["actions"] if action != "visual"
        ]
        grant = LiveGrant(TaskScope.parse(payload))
        self.service._grants[task] = (self.service._grants[task][0], grant)
        self.service.backend._sessions[session].grant = grant
        with patch.object(self.service.backend, "capture_page") as capture:
            with self.assertRaises(TaskPermissionError):
                self.capture(task, session)
            capture.assert_not_called()

    def test_hidden_tab_requires_a_visible_view(self):
        window, _ = self.window()
        task, _, session = self.share(window)
        window._new_tab(url="about:blank")
        with self.assertRaises(PageInspectionError):
            self.capture(task, session)

    def test_take_back_during_capture_releases_no_pixels(self):
        window, view = self.window()
        task, _, session = self.share(window)
        future = self.service.broker.submit(
            "page.capture", task_id=task, session_id=session
        )
        self.wait_for(lambda: bool(self.service.backend._sessions[session].waits))
        window._take_back_current_tab()
        self.wait_for(future.done)
        with self.assertRaises(PageInspectionError):
            future.result()
        self.assertIsNone(view.page().devToolsPage())
        self.assertFalse(
            any(
                event.kind == "page.captured"
                for event in self.service.store.events(task)
            )
        )

    def test_restricted_page_capture_requires_a_watch_view_and_keeps_scripts_disabled(
        self,
    ):
        task = self.task()
        record = self.command(
            "grant.request",
            task_id=task,
            scope={
                "origins": [self.origin],
                "actions": ["navigate", "read", "visual", "attach"],
                "allow_private_network": True,
            },
        )
        self.service.approve_grant(task, record.grant_id)
        session = self.command("session.open", task_id=task).session_id
        self.command(
            "session.navigate",
            task_id=task,
            session_id=session,
            url=self.origin + "/private",
        )
        self.wait_for(
            lambda: self.service.backend.inspect(session).load_success is True
        )
        with self.assertRaises(PageInspectionError):
            self.capture(task, session)
        self.command("session.watch", task_id=task, session_id=session)
        evidence = self.capture(task, session)
        self.assertGreater(evidence["width"], 0)
        self.assertIn("viewport_only", evidence["limitations"])


class ImagePayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qt_test_support import application

        cls.app = application()

    def jpeg(self, width=64, height=32):
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(0xFF112233)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "JPEG")
        return base64.b64encode(bytes(buffer.data())).decode()

    def test_large_image_is_scaled_and_image_content_is_not_duplicated(self):
        result = image_payload(
            json.dumps({"data": self.jpeg(1920, 1080), "viewport_clipped": False})
        )
        self.assertEqual((result["width"], result["height"]), (1280, 720))
        response = tool_result({"result": result}, images=True)
        self.assertEqual([part.type for part in response.content], ["text", "image"])
        self.assertEqual(
            json.loads(response.content[0].text), response.structuredContent
        )

    def test_malformed_and_oversized_images_are_rejected(self):
        for raw in (
            "null",
            "{}",
            json.dumps({"data": "broken", "viewport_clipped": False}),
            json.dumps({"data": self.jpeg(4097, 1), "viewport_clipped": False}),
            "x" * 1600001,
        ):
            with self.subTest(raw=raw[:30]):
                with self.assertRaises((PageInspectionError, ValueError)):
                    image_payload(raw)

    def test_other_tool_payloads_and_errors_keep_existing_shape(self):
        value = {"result": {"image": "service data"}}
        response = tool_result(value)
        self.assertEqual(response.structuredContent, value)
        self.assertEqual([part.type for part in response.content], ["text"])
        error = tool_result({"error": {"code": "approval_required"}}, images=True)
        self.assertTrue(error.isError)
        self.assertEqual(len(error.content), 1)
