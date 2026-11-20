"""Actual-page handoff, normal scripts and immediate, irreversible Take Back."""

import unittest

import test_task_profiles as fixtures
from PyQt6 import sip
from PyQt6.QtCore import QEvent, Qt, QUrl
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWebEngineCore import QWebEngineSettings

from browser.browser_window import BrowserWindow
from services.page_inspector import PageInspectionError
from services.task_client import TaskClient
from services.task_policy import TaskPermissionError, TaskScope

HTML = """<html><title>Live fixture</title><body>
<h1>Current live document</h1><input id="field" aria-label="Message" value="PRIVATE_VALUE">
<input type="checkbox" aria-label="Confirm"><form id="login"><input type="password" value="PASSWORD_VALUE"></form>
<button type="button" id="action" onclick="window.clicked++; document.title='Clicked'; fetch('/background', {method:'POST',body:'live'})">Run action</button>
<form action="/submitted" method="post" onsubmit="event.preventDefault(); window.sent++; document.title='Submitted'">
<button id="send">Send</button></form>
<div role="button" aria-label="Custom">Custom</div><canvas></canvas><img alt="Picture">
<div id="shadow"></div>
<script>window.ticks=0; window.clicked=0; window.sent=0;
setInterval(()=>window.ticks++, 20);
document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML='<p>Shadow content</p>';
</script></body></html>"""


class LiveTabsTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskProfileTests.tearDownClass.__func__)
    setUp = fixtures.TaskProfileTests.setUp
    tearDown = fixtures.TaskProfileTests.tearDown
    wait_for = fixtures.TaskProfileTests.wait_for
    command = fixtures.TaskProfileTests.command

    def window(self, profile="work"):
        window = BrowserWindow()
        self.windows.append(window)
        window._switch_to_profile(profile)
        window.show()
        self.page_fixtures["/live"] = HTML.encode()
        view = window.tabs[window.current_tab_index]
        loaded = []
        view.loadFinished.connect(loaded.append)
        view.setUrl(QUrl(self.origin + "/live"))
        self.wait_for(lambda: loaded and view.title() == "Live fixture")
        view.original_policy = view.focusPolicy()
        return window, view

    def task(self):
        return self.command(
            "task.create", objective="Collaborate on the live page"
        ).task_id

    def share(self, window, task=None):
        task = task or self.task()
        grant = window.delegate_tabs(task)
        if grant.status == "requested":
            self.service.approve_grant(task, grant.grant_id)
        session = self.service.backend.live_session(
            window.tabs[window.current_tab_index]
        )
        self.assertIsNotNone(session)
        return task, grant, session

    def value(self, page, expression):
        values = []
        page.runJavaScript(expression, values.append)
        self.wait_for(lambda: bool(values))
        return values[0]

    def inspect(self, task, session):
        return self.command("page.inspect", task_id=task, session_id=session)

    def act(self, task, session, snapshot, target, operation="activate", **extra):
        return self.command(
            "page.act",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
            operation=operation,
            **extra,
        )

    def test_buttons_near_a_filled_password_or_card_are_left_to_you(self):
        window, view = self.window()
        self.value(
            view.page(),
            """const box = document.createElement('div');
            box.innerHTML = '<input id="pw" type="password">' +
                '<div role="button" aria-label="Sign in" onclick="window.sent++">Go</div>';
            document.body.append(box); true;""",
        )
        task, _, session = self.share(window)

        def press():
            snapshot = self.inspect(task, session)
            target = next(
                t for f in snapshot.frames for t in f.targets if t.label == "Sign in"
            )
            self.act(task, session, snapshot, target)

        press()  # Empty: an ordinary button.
        self.wait_for(lambda: self.value(view.page(), "window.sent") == 1)
        # A reveal toggle that made it plain text does not change what it is.
        self.value(
            view.page(),
            "pw.value = 'typed by you'; pw.setAttribute('type', 'text'); "
            "pw.setAttribute('type', 'password'); true",
        )
        with self.assertRaises(PageInspectionError) as raised:
            press()
        self.assertEqual(raised.exception.code, "sensitive_form_unsupported")
        self.assertEqual(self.value(view.page(), "window.sent"), 1)

    def test_live_changed_form_help_and_errors_require_fresh_target_references(self):
        window, view = self.window()
        self.value(
            view.page(),
            """document.querySelector('#field').setAttribute('aria-describedby','hint');
            document.querySelector('#field').setAttribute('aria-invalid','true');
            document.querySelector('#field').setAttribute('aria-errormessage','error');
            const help = document.createElement('div'); help.id='help';
            help.innerHTML='<p id="hint">Original help</p><p id="error">Original error</p>';
            document.body.append(help); true;""",
        )
        task, _, session = self.share(window)
        for mutation in (
            "document.querySelector('#hint').textContent='New help'",
            "document.querySelector('#error').textContent='New error'",
            "document.querySelector('#help').hidden=true",
        ):
            snapshot = self.inspect(task, session)
            field = next(t for t in snapshot.frames[0].targets if t.label == "Message")
            self.value(view.page(), mutation + "; true")
            with self.assertRaises(PageInspectionError) as error:
                self.command(
                    "page.target",
                    task_id=task,
                    session_id=session,
                    snapshot_id=snapshot.snapshot_id,
                    target_id=field.target_id,
                )
            self.assertEqual(error.exception.code, "stale_snapshot")
        fresh = self.inspect(task, session)
        field = next(t for t in fresh.frames[0].targets if t.label == "Message")
        self.assertEqual(field.description, "")
        self.assertEqual(field.error_message, "")
        self.assertEqual(
            self.value(view.page(), "document.querySelector('#field').value"),
            "PRIVATE_VALUE",
        )

    def test_live_oversized_referenced_help_reports_unavailable_target_context(self):
        window, view = self.window()
        self.value(
            view.page(),
            """document.querySelector('#action').setAttribute('aria-describedby','help');
            const help = document.createElement('p'); help.id='help';
            help.textContent='a'.repeat(66000); document.body.append(help); true;""",
        )
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        target = next(t for t in snapshot.frames[0].targets if t.label == "Run action")
        self.assertEqual(target.operations, ())
        self.assertIn("live_target_context_limit", target.constraints)
        self.assertIn("live_target_context_limit", snapshot.limitations)
        with self.assertRaises(PageInspectionError):
            self.act(task, session, snapshot, target)
        self.assertEqual(self.value(view.page(), "window.clicked"), 0)

    def test_live_unrelated_changes_preserve_targets_with_form_metadata(self):
        window, view = self.window()
        self.value(
            view.page(),
            """document.querySelector('#action').setAttribute('aria-describedby','help');
            const help = document.createElement('p'); help.id='help'; help.textContent='Action help';
            document.body.append(help); true;""",
        )
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        target = next(t for t in snapshot.frames[0].targets if t.label == "Run action")
        self.assertEqual(target.description, "Action help")
        self.value(
            view.page(),
            "document.querySelector('h1').textContent='Unrelated clock'; true",
        )
        result = self.act(task, session, snapshot, target)
        self.assertEqual(result.status, "dispatched")
        self.wait_for(lambda: self.value(view.page(), "window.clicked") == 1)

    def test_approval_preserves_original_page_state_and_normal_scripts(self):
        window, view = self.window()
        page, profile, parent = view.page(), view.page().profile(), view.parent()
        self.value(
            page, "document.querySelector('#field').value='EDITED_BY_USER'; true"
        )
        task = self.task()
        grant = window.delegate_tabs(task)
        self.assertIsNone(self.service.backend.live_session(view))
        with self.assertRaises(TaskPermissionError):
            self.command("browser.tabs", task_id=task)
        self.service.approve_grant(task, grant.grant_id)
        session = self.service.backend.live_session(view)
        self.assertIs(view.page(), page)
        self.assertIs(page.profile(), profile)
        self.assertIs(view.parent(), parent)
        self.assertEqual(
            self.value(page, "document.querySelector('#field').value"), "EDITED_BY_USER"
        )
        self.assertTrue(
            page.settings().testAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled
            )
        )
        before = self.value(page, "window.ticks")
        self.wait_for(lambda: self.value(page, "window.ticks") > before)
        self.assertTrue(window.live_bar.isVisible())
        snapshot = self.inspect(task, session)
        self.assertEqual(self.service.backend.inspect(session).mode, "live")
        self.assertIn("normal_website_scripts_and_network", snapshot.limitations)
        self.assertNotIn("document_scripts_disabled", snapshot.limitations)
        self.assertNotIn("EDITED_BY_USER", repr(snapshot))
        self.assertNotIn("PASSWORD_VALUE", repr(snapshot))
        self.assertIn("open_shadow_root_present", snapshot.limitations)
        self.assertIn("canvas_present", snapshot.limitations)
        self.assertIn("visual_content_present", snapshot.limitations)
        # Live custom widgets are actionable through verified pointer clicks.
        self.assertNotIn("custom_controls_present", snapshot.limitations)
        custom = next(t for t in snapshot.frames[0].targets if t.label == "Custom")
        self.assertEqual(custom.operations, ("activate",))
        self.assertIn("pointer_activation", custom.constraints)

    def test_native_button_activation_runs_website_handler_and_background_post(self):
        window, view = self.window()
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        target = next(t for t in snapshot.frames[0].targets if t.label == "Run action")
        result = self.act(task, session, snapshot, target)
        self.assertEqual(result.status, "dispatched")
        self.assertEqual(result.input_semantics, "chromium_keyboard")
        self.wait_for(lambda: self.value(view.page(), "window.clicked") == 1)
        self.wait_for(
            lambda: any(path == "POST /background" for path, _ in self.requests)
        )
        self.assertTrue(
            view.page()
            .settings()
            .testAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled)
        )
        self.assertIsNotNone(view.page().devToolsPage())
        window.take_back_button.click()
        self.assertIsNone(view.page().devToolsPage())
        self.assertEqual(view.title(), "Clicked")

    def test_custom_widgets_are_clicked_only_where_visibly_unobscured(self):
        window, view = self.window()
        self.value(
            view.page(),
            """
            const add = html => { const d = document.createElement('div');
              d.innerHTML = html; document.body.append(d.firstElementChild); };
            window.tabClicks = 0; window.coverClicks = 0;
            add('<div role="tab" aria-label="Billing" style="display:inline-block;padding:8px">Billing</div>');
            document.querySelector('[aria-label=Billing]').addEventListener('click',
              () => { window.tabClicks++; document.title = 'Billing tab'; });
            add('<div style="position:relative;height:40px"><div role="switch" aria-label="Alerts" style="padding:8px">Alerts</div>' +
                '<div id="cover" style="position:absolute;inset:0;background:#fff8"></div></div>');
            document.querySelector('#cover').addEventListener('click', () => window.coverClicks++);
            true;
            """,
        )
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        targets = {t.label: t for t in snapshot.frames[0].targets}
        result = self.act(task, session, snapshot, targets["Billing"])
        self.assertEqual(result.status, "dispatched")
        self.assertEqual(result.input_semantics, "chromium_pointer")
        self.wait_for(lambda: self.value(view.page(), "window.tabClicks") == 1)
        snapshot = self.inspect(task, session)
        targets = {t.label: t for t in snapshot.frames[0].targets}
        with self.assertRaises(PageInspectionError) as error:
            self.act(task, session, snapshot, targets["Alerts"])
        self.assertEqual(error.exception.code, "target_obscured")
        self.assertEqual(self.value(view.page(), "window.coverClicks"), 0)

    def test_scroll_then_settled_wait_reads_lazily_loaded_content(self):
        window, view = self.window()
        self.value(
            view.page(),
            """
            document.body.innerHTML = '<ul id="feed"></ul>';
            const feed = document.getElementById('feed');
            const add = (from, count) => { for (let i = from; i < from + count; i++) {
              const li = document.createElement('li'); li.textContent = 'Story ' + i;
              li.style.height = '60px'; feed.append(li); } };
            add(0, 30); let loading = false;
            addEventListener('scroll', () => {
              if (loading || innerHeight + scrollY < document.body.scrollHeight - 100) return;
              loading = true;
              setTimeout(() => { add(feed.children.length, 20); loading = false; }, 600);
            });
            true;
            """,
        )
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        frame = snapshot.frames[0]
        self.assertIn("more_content_below", snapshot.limitations)
        self.assertGreater(frame.scroll.height, frame.scroll.viewport_height)
        self.assertNotIn("Story 35", frame.text)
        self.command(
            "page.scroll",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            frame_id=frame.frame_id,
            dx=0,
            dy=10000,
        )
        waited = self.command(
            "page.wait",
            task_id=task,
            session_id=session,
            condition="settled",
            timeout_ms=5000,
        )
        self.assertEqual(waited.status, "matched")
        after = self.inspect(task, session)
        self.assertIn("Story 35", after.frames[0].text)
        self.assertGreater(after.frames[0].scroll.y, 0)

    def test_live_buttons_inside_shadow_roots_can_be_activated(self):
        window, view = self.window()
        self.value(
            view.page(),
            """
            const root = document.querySelector('#shadow').shadowRoot;
            root.innerHTML = '<button id="save">Save draft</button>';
            window.saved = 0;
            root.querySelector('#save').addEventListener('click', () => window.saved++);
            true;
            """,
        )
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        target = next(t for t in snapshot.frames[0].targets if t.label == "Save draft")
        self.assertEqual(target.operations, ("activate",))
        result = self.act(task, session, snapshot, target)
        self.assertEqual(result.status, "dispatched")
        self.wait_for(lambda: self.value(view.page(), "window.saved") == 1)

    def test_live_shadow_labels_and_host_changes_revalidate_target_references(self):
        window, view = self.window()
        self.value(
            view.page(),
            """
            const root = document.querySelector('#shadow').shadowRoot;
            root.innerHTML='<span id="name">Live component action</span><button aria-labelledby="name">Icon</button>';
            true;
        """,
        )
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        button = next(
            t for t in snapshot.frames[0].targets if t.label == "Live component action"
        )
        self.assertEqual(button.operations, ("activate",))
        self.assertIn("shadow_control", button.constraints)
        self.value(
            view.page(),
            "document.querySelector('#shadow').shadowRoot.querySelector('#name').textContent='Changed action'; true",
        )
        with self.assertRaises(PageInspectionError):
            self.command(
                "page.target",
                task_id=task,
                session_id=session,
                snapshot_id=snapshot.snapshot_id,
                target_id=button.target_id,
            )
        snapshot = self.inspect(task, session)
        button = next(
            t for t in snapshot.frames[0].targets if t.label == "Changed action"
        )
        self.value(view.page(), "document.querySelector('#shadow').hidden=true; true")
        with self.assertRaises(PageInspectionError):
            self.command(
                "page.target",
                task_id=task,
                session_id=session,
                snapshot_id=snapshot.snapshot_id,
                target_id=button.target_id,
            )
        self.assertNotIn("Changed action", repr(self.inspect(task, session)))

    def test_live_form_submit_runs_handlers_without_claiming_site_success(self):
        window, view = self.window()
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        target = next(t for t in snapshot.frames[0].targets if t.label == "Send")
        result = self.command(
            "page.submit",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
        )
        self.assertEqual(result.status, "dispatched")
        self.assertEqual(result.site_effects, "unknown")
        self.assertFalse(result.request_authorized)
        self.assertEqual(self.value(view.page(), "window.sent"), 1)

    def test_take_back_removes_access_and_cannot_be_re_adopted(self):
        window, view = self.window()
        task, _, session = self.share(window)
        tabs = self.command("browser.tabs", task_id=task)["tabs"]
        self.assertEqual([tab["tab_id"] for tab in tabs], [view.agent_tab_id])
        adopted = self.command("session.adopt", task_id=task, tab_id=view.agent_tab_id)
        self.assertEqual(adopted.session_id, session)
        policy = view.original_policy
        self.assertEqual(view.focusPolicy(), Qt.FocusPolicy.NoFocus)
        window.take_back_button.click()
        self.assertEqual(view.focusPolicy(), policy)
        self.assertFalse(window.live_bar.isVisible())
        self.assertFalse(sip.isdeleted(view.page()))
        self.assertEqual(self.command("browser.tabs", task_id=task)["tabs"], [])
        with self.assertRaises(TaskPermissionError):
            self.command("session.adopt", task_id=task, tab_id=view.agent_tab_id)
        with self.assertRaises(TaskPermissionError):
            self.inspect(task, session)
        self.assertEqual(self.service.store.get(task).status, "running")

    def test_user_input_is_shielded_until_take_back(self):
        window, view = self.window()
        task, _, session = self.share(window)
        shield = self.service.backend._sessions[session].shield
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a"
        )
        self.assertTrue(shield.eventFilter(view, event))
        window._take_back_current_tab()
        self.assertTrue(sip.isdeleted(shield))
        self.assertEqual(view.focusPolicy(), view.original_policy)

    def test_take_back_cancels_pending_input_before_dispatch(self):
        window, view = self.window()
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        target = next(t for t in snapshot.frames[0].targets if t.label == "Run action")
        future = self.service.broker.submit(
            "page.act",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
            operation="activate",
        )
        self.wait_for(
            lambda: self.service.backend._sessions[session].pending_action is not None
        )
        window._take_back_current_tab()
        self.wait_for(future.done)
        with self.assertRaises(PageInspectionError):
            future.result()
        self.assertEqual(self.value(view.page(), "window.clicked"), 0)
        self.assertIsNone(view.page().devToolsPage())

    def test_take_back_while_loading_does_not_stop_user_page(self):
        window, view = self.window()
        task, _, session = self.share(window)
        self.command(
            "session.navigate",
            task_id=task,
            session_id=session,
            url=self.origin + "/slow",
        )
        window._take_back_current_tab()
        self.wait_for(lambda: view.title() == "Task fixture")
        self.wait_for(lambda: not view.page().isLoading())

    def test_watch_uses_original_window_without_reparenting_the_view(self):
        window, view = self.window()
        task, _, session = self.share(window)
        parent = view.parent()
        result = self.command("session.watch", task_id=task, session_id=session)
        self.assertEqual(result["mode"], "live")
        self.assertIs(view.parent(), parent)
        self.assertNotIn(session, self.service._watchers)

    def test_close_session_and_task_cancel_return_tabs_without_destruction(self):
        for cancel in (False, True):
            window, view = self.window()
            task, _, session = self.share(window)
            page = view.page()
            if cancel:
                self.service.cancel_task(task)
            else:
                self.command("session.close", task_id=task, session_id=session)
            self.assertIs(view.page(), page)
            self.assertFalse(sip.isdeleted(page))
            self.assertIsNone(self.service.backend.live_session(view))
            self.assertNotIn(view.agent_tab_id, self.service.live_tabs.offers)
            self.assertFalse(window.live_bar.isVisible())

    def test_unshared_tabs_cross_task_and_incognito_are_not_exposed(self):
        window, view = self.window()
        other_window, other_view = self.window()
        task, _, _ = self.share(window)
        other = self.task()
        with self.assertRaises(TaskPermissionError):
            self.command("session.adopt", task_id=other, tab_id=view.agent_tab_id)
        with self.assertRaises(TaskPermissionError):
            self.command("session.adopt", task_id=task, tab_id=other_view.agent_tab_id)
        self.assertEqual(len(self.command("browser.tabs", task_id=task)["tabs"]), 1)
        other_window._switch_to_profile("search")
        with self.assertRaises(ValueError):
            other_window.delegate_tabs(other)
        # Agent-created private live sessions are allowed; they confer no
        # access to the user's shared off-the-record search profile.
        scope = TaskScope.parse({"origins": [], "actions": ["live"]})
        self.assertEqual(scope.profile, "private")

    def test_expiry_and_profile_switch_restore_control(self):
        window, view = self.window()
        task, _, session = self.share(window)
        self.service._grants[task][1].deadline = 0
        self.service._expire_grants()
        self.assertIsNone(self.service.backend.live_session(view))
        self.assertFalse(sip.isdeleted(view.page()))
        self.assertEqual(view.focusPolicy(), view.original_policy)
        task, _, session = self.share(window)
        window._switch_to_profile("default")
        self.assertNotIn(session, self.service._sessions)
        self.assertNotIn(view.agent_tab_id, self.service.live_tabs.offers)

    def test_whole_profile_selection_shares_current_tabs_across_windows_only(self):
        first, first_view = self.window()
        second, second_view = self.window()
        second_view.tab_id = first_view.tab_id  # Same saved state in two windows.
        third, third_view = self.window("default")
        task = self.task()
        grant = first.delegate_tabs(task, whole_profile=True)
        self.service.approve_grant(task, grant.grant_id)
        tabs = self.command("browser.tabs", task_id=task)["tabs"]
        self.assertEqual(
            {tab["tab_id"] for tab in tabs},
            {first_view.agent_tab_id, second_view.agent_tab_id},
        )
        self.assertTrue(first.live_bar.isVisible())
        self.assertTrue(second.live_bar.isVisible())
        self.assertFalse(third.live_bar.isVisible())
        first._new_tab(url=self.origin + "/live")
        self.assertEqual(len(self.command("browser.tabs", task_id=task)["tabs"]), 2)
        self.assertIsNone(self.service.backend.live_session(third_view))
        second.close()
        self.assertEqual(len(self.command("browser.tabs", task_id=task)["tabs"]), 1)

    def test_user_navigation_returns_control_before_address_change(self):
        window, view = self.window()
        task, _, session = self.share(window)
        window._navigate_to_url(self.origin + "/next")
        self.assertNotIn(session, self.service._sessions)
        self.assertFalse(window.live_bar.isVisible())
        self.wait_for(lambda: not view.page().isLoading())

    def test_replacement_requires_fresh_review_and_old_rules_do_not_grant_live(self):
        window, view = self.window()
        task = self.task()
        record = self.command(
            "grant.request",
            task_id=task,
            scope={
                "origins": [self.origin],
                "profile": "existing:work",
                "allow_private_network": True,
                "actions": ["read", "inspect", "navigate", "attach"],
            },
        )
        self.service.approve_grant(task, record.grant_id, remember="profile")
        page_session = self.command(
            "session.open", task_id=task, profile="existing:work"
        )
        request = window.delegate_tabs(task)
        self.assertEqual(request.status, "requested")
        self.assertNotIn(page_session.session_id, self.service._sessions)
        self.assertIsNone(self.service.backend.live_session(view))
        self.service.deny_grant(task, request.grant_id)
        self.assertFalse(window.live_bar.isVisible())
        self.assertFalse(sip.isdeleted(view.page()))

    def test_live_target_ignores_unrelated_updates_but_rejects_changed_controls(self):
        window, view = self.window()
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        target = next(t for t in snapshot.frames[0].targets if t.label == "Run action")
        self.value(
            view.page(), "document.querySelector('h1').textContent='Live clock'; true"
        )
        result = self.act(task, session, snapshot, target)
        self.assertEqual(result.status, "dispatched")
        snapshot = self.inspect(task, session)
        target = next(t for t in snapshot.frames[0].targets if t.label == "Run action")
        self.value(
            view.page(),
            "document.querySelector('#action').textContent='Changed action'; true",
        )
        with self.assertRaises(PageInspectionError):
            self.act(task, session, snapshot, target)
        self.assertEqual(self.value(view.page(), "window.clicked"), 1)

    def test_navigation_outside_scope_returns_tab_and_hides_destination(self):
        window, view = self.window()
        task, _, session = self.share(window)
        url = self.origin.replace("127.0.0.1", "localhost") + "/outside"
        self.value(view.page(), "location.href=" + repr(url))
        self.wait_for(lambda: session not in self.service._sessions)
        self.assertFalse(window.live_bar.isVisible())
        self.assertNotIn(view.agent_tab_id, self.service.live_tabs.offers)
        with self.assertRaises(TaskPermissionError):
            self.command("session.inspect", task_id=task, session_id=session)
        self.assertNotIn(url, repr(self.service.store.events(task)))
        self.wait_for(lambda: not view.page().isLoading())
        self.assertFalse(sip.isdeleted(view.page()))

    def test_pending_tab_offer_cannot_be_stolen_and_deny_releases_selection(self):
        window, view = self.window()
        task = self.task()
        grant = window.delegate_tabs(task)
        other = self.task()
        with self.assertRaises(ValueError):
            window.delegate_tabs(other)
        self.service.deny_grant(task, grant.grant_id)
        self.assertNotIn(view.agent_tab_id, self.service.live_tabs.offers)
        other_grant = window.delegate_tabs(other)
        self.assertEqual(other_grant.status, "requested")

    def test_live_scope_without_trusted_tab_selection_exposes_no_tabs(self):
        window, view = self.window()
        task = self.task()
        grant = self.command(
            "grant.request",
            task_id=task,
            scope={
                "origins": [self.origin],
                "actions": ["live", "read", "inspect"],
                "profile": "existing:work",
                "allow_private_network": True,
            },
        )
        self.service.approve_grant(task, grant.grant_id)
        self.assertEqual(self.command("browser.tabs", task_id=task)["tabs"], [])
        with self.assertRaises(TaskPermissionError):
            self.command("session.adopt", task_id=task, tab_id=view.agent_tab_id)

    def test_task_client_cannot_request_trusted_handoff_or_approval(self):
        client = TaskClient(self.task(), self.service.broker)
        future = client.submit("live_tabs.request", views=[])
        self.wait_for(future.done)
        with self.assertRaises(TaskPermissionError):
            future.result()
