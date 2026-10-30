"""Live cross-origin frame actions, hover and scrolling inner containers."""

import unittest

import test_page_inspection as page_fixture

from services.page_inspector import PageInspectionError

FRAME = """<!doctype html><title>Embedded</title><body style='margin:0'>
<h2>Embedded checkout</h2>
<label>Card name <input id='name'></label>
<label><input type='checkbox' id='save'> Save card</label>
<div role='button' id='pay' style='padding:6px;display:inline-block'>Pay now</div>
<form action='/paid' method='post'><button>Submit order</button></form>
<script>window.pays = 0; pay.onclick = () => window.pays++;</script>
</body>"""

PAGE = """<!doctype html><title>Main page</title><body style='margin:0'>
<h1>Shop</h1>
<div id='menu' style='padding:6px;display:inline-block'>
  <button type='button' aria-label='Account'>Account</button>
  <a href='/signout' id='signout' hidden>Sign out</a></div>
<div id='panel' style='height:60px;overflow:auto;border:1px solid'>
  <p style='height:400px'>Long list</p><button type='button'>Inside panel</button></div>
<iframe id='frame' src='{other}/embedded' style='border:0;width:400px;height:200px'></iframe>
<iframe src='{other}/outer' style='border:0;width:400px;height:120px'></iframe>
<script>
menu.addEventListener('mouseenter', () => { signout.hidden = false; });
</script></body>"""

OUTER = """<!doctype html><body><iframe src='{origin}/inner'></iframe></body>"""


class AgentFrameTests(unittest.TestCase):
    setUpClass = classmethod(page_fixture.PageInspectionTests.setUpClass.__func__)
    tearDownClass = classmethod(page_fixture.PageInspectionTests.tearDownClass.__func__)
    setUp = page_fixture.PageInspectionTests.setUp
    tearDown = page_fixture.PageInspectionTests.tearDown
    wait_for = page_fixture.PageInspectionTests.wait_for
    command = page_fixture.PageInspectionTests.command
    request = page_fixture.PageInspectionTests.request
    load = page_fixture.PageInspectionTests.load

    def session(self, *, live=True):
        other = f"http://localhost:{self.server.server_port}"
        type(self).page_fixtures["/embedded"] = FRAME.encode()
        type(self).page_fixtures["/outer"] = OUTER.replace(
            "{origin}", self.origin
        ).encode()
        type(self).page_fixtures["/inner"] = b"<button type='button'>Deep</button>"
        task = self.command("task.create", objective="Act inside embedded frames")
        actions = ["navigate", "inspect", "read", "attach", "edit", "scroll", "submit"]
        if live:
            actions.append("live")
        grant = self.request(
            task.task_id,
            actions=actions,
            origins=[self.origin, other],
            max_operations=200,
        )
        if grant.status == "requested":
            self.service.approve_grant(task.task_id, grant.grant_id)
        opened = self.command(
            "session.open",
            task_id=task.task_id,
            mode="live" if live else "restricted",
        )
        page = self.service.backend._sessions[opened.session_id].page
        self.load(opened.session_id, page, PAGE.replace("{other}", other))
        if live:
            self.wait_for(
                lambda: self.frame_value(page, "document.readyState") == "complete"
            )
        self.command(
            "session.watch", task_id=task.task_id, session_id=opened.session_id
        )
        self.app.processEvents()
        return task.task_id, opened.session_id, page

    def frame_value(self, page, code):
        frames = page.mainFrame().children()
        if not frames:
            return None
        results = []
        frames[0].runJavaScript(code, results.append)
        self.wait_for(lambda: bool(results))
        return results[0]

    def value(self, page, code):
        results = []
        page.runJavaScript(code, results.append)
        self.wait_for(lambda: bool(results))
        return results[0]

    def inspect(self, task, session):
        return self.command("page.inspect", task_id=task, session_id=session)

    def find(self, snapshot, label):
        return next(
            t for f in snapshot.frames for t in f.targets if t.label.startswith(label)
        )

    def act(self, task, session, snapshot, target, operation, **extra):
        return self.command(
            "page.act",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
            operation=operation,
            **extra,
        )

    def test_live_cross_origin_frame_controls_are_actionable(self):
        task, session, page = self.session()
        snapshot = self.inspect(task, session)
        embedded = next(f for f in snapshot.frames if "Embedded checkout" in f.text)
        self.assertEqual(embedded.limitations, ("cross_origin_frame",))
        name = self.find(snapshot, "Card name")
        self.assertIn("cross_origin_frame", name.constraints)
        self.assertEqual(name.frame_id, embedded.frame_id)
        result = self.act(task, session, snapshot, name, "enter", text="Ada Lovelace")
        self.assertEqual(result.status, "applied")
        self.assertEqual(
            self.frame_value(page, "document.getElementById('name').value"),
            "Ada Lovelace",
        )
        snapshot = self.inspect(task, session)
        result = self.act(
            task, session, snapshot, self.find(snapshot, "Save card"), "activate"
        )
        self.assertEqual(result.checked, True)
        snapshot = self.inspect(task, session)
        pay = self.find(snapshot, "Pay now")
        self.assertIn("pointer_activation", pay.constraints)
        self.act(task, session, snapshot, pay, "activate")
        self.wait_for(lambda: self.frame_value(page, "window.pays") == 1)

    def test_frame_forms_stay_main_document_only(self):
        task, session, _ = self.session()
        snapshot = self.inspect(task, session)
        submit = self.find(snapshot, "Submit order")
        self.assertNotIn("submit", submit.operations)
        self.assertIn("frame_form_unsupported", submit.constraints)
        with self.assertRaises(PageInspectionError):
            self.command(
                "page.submit",
                task_id=task,
                session_id=session,
                snapshot_id=snapshot.snapshot_id,
                target_id=submit.target_id,
            )

    def test_nested_frames_cannot_be_clicked_through(self):
        task, session, _ = self.session()
        snapshot = self.inspect(task, session)
        deep = [t for f in snapshot.frames for t in f.targets if t.label == "Deep"]
        # The same-origin frame inside a cross-origin frame stays unread; any
        # pointer action needs a frame directly inside the page.
        for target in deep:
            self.assertNotIn("pointer_activation", target.operations)

    def test_restricted_sessions_keep_cross_origin_frames_read_only(self):
        task, session, _ = self.session(live=False)
        frames = []

        def read():
            frames[:] = self.inspect(task, session).frames
            return any("Embedded checkout" in f.text for f in frames)

        self.wait_for(read)
        embedded = next(f for f in frames if "Embedded checkout" in f.text)
        self.assertEqual(embedded.limitations, ("cross_origin_frame_read_only",))
        self.assertEqual(embedded.targets, ())

    def test_hover_reveals_menus(self):
        task, session, page = self.session()
        snapshot = self.inspect(task, session)
        self.assertFalse(
            any(t.label == "Sign out" for f in snapshot.frames for t in f.targets)
        )
        result = self.act(
            task, session, snapshot, self.find(snapshot, "Account"), "hover"
        )
        self.assertEqual(result.input_semantics, "chromium_pointer")
        self.wait_for(lambda: self.value(page, "!signout.hidden"))
        snapshot = self.inspect(task, session)
        self.assertTrue(any(t.label == "Sign out" for t in snapshot.frames[0].targets))

    def test_hover_needs_a_live_session(self):
        task, session, _ = self.session(live=False)
        snapshot = self.inspect(task, session)
        with self.assertRaises(PageInspectionError) as error:
            self.act(task, session, snapshot, self.find(snapshot, "Account"), "hover")
        self.assertEqual(error.exception.code, "live_session_required")

    def scroll(self, task, session, snapshot, **reference):
        return self.command(
            "page.scroll",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            dx=0,
            dy=120,
            **reference,
        )

    def test_scrolling_an_inner_panel_and_a_cross_origin_frame(self):
        task, session, page = self.session()
        snapshot = self.inspect(task, session)
        inside = self.find(snapshot, "Inside panel")
        result = self.scroll(task, session, snapshot, target_id=inside.target_id)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.scroll_position, (0, 120))
        self.assertEqual(
            result.container_size[1], self.value(page, "panel.scrollHeight")
        )
        self.assertEqual(self.value(page, "panel.scrollTop"), 120)
        # A target whose only scroller is the page itself has no container.
        snapshot = self.inspect(task, session)
        with self.assertRaises(PageInspectionError) as error:
            self.scroll(
                task,
                session,
                snapshot,
                target_id=self.find(snapshot, "Account").target_id,
            )
        self.assertEqual(error.exception.code, "scroll_container_unavailable")
        self.frame_value(page, "document.body.style.height = '2000px'; true")
        snapshot = self.inspect(task, session)
        embedded = next(f for f in snapshot.frames if "Embedded checkout" in f.text)
        result = self.scroll(task, session, snapshot, frame_id=embedded.frame_id)
        self.assertEqual(result.scroll_position, (0, 120))
        self.assertEqual(self.frame_value(page, "window.scrollY"), 120)
        with self.assertRaises(ValueError):
            self.scroll(
                task,
                session,
                snapshot,
                frame_id=embedded.frame_id,
                target_id=inside.target_id,
            )


if __name__ == "__main__":
    unittest.main()
