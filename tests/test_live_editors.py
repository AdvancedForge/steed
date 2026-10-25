"""Rich-text boundaries are observable without values; live entry stays scoped."""

import json
import unittest

import test_live_tabs as live
import test_page_inspection as inspection
from PyQt6.QtWebEngineCore import QWebEnginePage

from services.page_inspector import PageInspectionError
from services.task_policy import TaskPermissionError


class LiveEditorTests(unittest.TestCase):
    setUpClass = classmethod(live.LiveTabsTests.setUpClass.__func__)
    tearDownClass = classmethod(live.LiveTabsTests.tearDownClass.__func__)
    setUp = live.LiveTabsTests.setUp
    tearDown = live.LiveTabsTests.tearDown
    wait_for = live.LiveTabsTests.wait_for
    command = live.LiveTabsTests.command
    window = live.LiveTabsTests.window
    value = live.LiveTabsTests.value
    task = live.LiveTabsTests.task
    share = live.LiveTabsTests.share
    inspect = live.LiveTabsTests.inspect
    act = live.LiveTabsTests.act
    script = inspection.PageInspectionTests.script

    def opened(self, markup=None):
        window, view = self.window()
        markup = (
            markup
            or '<div id="editor" contenteditable="true" aria-label="Chat message" style="min-height:35px"><b>EXISTING_EDITOR_SECRET</b></div>'
        )
        self.value(
            view.page(),
            "document.body.insertAdjacentHTML('beforeend', "
            + json.dumps(markup)
            + "); true",
        )
        task, _, session = self.share(window)
        return window, view, task, session

    def editor(self, task, session, label="Chat message"):
        snapshot = self.inspect(task, session)
        target = next(t for t in snapshot.frames[0].targets if t.label == label)
        return snapshot, target

    def test_live_editor_entry_uses_trusted_editing_is_literal_and_can_be_undone(self):
        window, view, task, session = self.opened()
        snapshot, target = self.editor(task, session)
        self.assertEqual(target.kind, "editable")
        self.assertEqual(target.role, "textbox")
        self.assertEqual(target.operations, ("enter",))
        self.assertNotIn("EXISTING_EDITOR_SECRET", repr(snapshot))
        self.value(
            view.page(),
            "window.editorEvents=[]; editor.addEventListener('input', e=>editorEvents.push({trusted:e.isTrusted,type:e.inputType})); true",
        )
        text = "<b>Literal</b>\nSecond 🙂"
        outcome = self.act(task, session, snapshot, target, "enter", text=text)
        self.assertTrue(outcome.text_matches)
        self.assertTrue(outcome.changed)
        self.assertEqual(self.value(view.page(), "editor.innerText"), text)
        self.assertIn(
            "&lt;b&gt;Literal&lt;/b&gt;", self.value(view.page(), "editor.innerHTML")
        )
        events = self.value(view.page(), "editorEvents")
        self.assertTrue(events)
        self.assertTrue(all(event["trusted"] for event in events))
        window._take_back_tab(view)
        window.activateWindow()
        view.setFocus()
        self.value(view.page(), "editor.focus(); true")
        self.wait_for(
            lambda: view.page().action(QWebEnginePage.WebAction.Undo).isEnabled()
        )
        view.page().triggerAction(QWebEnginePage.WebAction.Undo)
        self.wait_for(
            lambda: (
                self.value(view.page(), "editor.innerText") == "EXISTING_EDITOR_SECRET"
            )
        )
        self.assertNotIn(
            "EXISTING_EDITOR_SECRET", repr(self.service.store.events(task))
        )
        self.assertNotIn("Second", repr(self.service.store.events(task)))

    def test_readonly_disabled_and_noneditable_boundaries_refuse_entry(self):
        markup = "".join(
            f'<div contenteditable="{editable}" aria-label="{name}" {extra}>PRIVATE_EDITOR_{name}</div>'
            for name, editable, extra in [
                ("Readonly", "true", 'aria-readonly="true"'),
                ("Disabled", "plaintext-only", 'aria-disabled="true"'),
            ]
        )
        _, view, task, session = self.opened(markup)
        for name in ("Readonly", "Disabled"):
            snapshot, target = self.editor(task, session, name)
            self.assertNotIn("PRIVATE_EDITOR", repr(snapshot))
            with self.assertRaises(PageInspectionError):
                self.act(task, session, snapshot, target, "enter", text="Replacement")
        self.assertEqual(
            self.value(
                view.page(), "document.querySelector('[aria-label=Readonly]').innerText"
            ),
            "PRIVATE_EDITOR_Readonly",
        )

    def test_noneditable_wrappers_preserve_controls_and_private_editor_islands_stay_omitted(
        self,
    ):
        _, _, task, session = self.opened("""
            <section contenteditable="false"><h2>Static wrapper text</h2>
            <button type="button" aria-label="Real action">Useful button</button>
            <div contenteditable="true" aria-label="Private editor">
              <span id="privateHint" contenteditable="false">PRIVATE_ISLAND_SECRET</span>
            </div></section>
            <input aria-label="Safe field" aria-describedby="privateHint">""")
        snapshot = self.inspect(task, session)
        targets = {t.label: t for t in snapshot.frames[0].targets}
        self.assertIn("Static wrapper text", snapshot.frames[0].text)
        self.assertEqual(targets["Real action"].operations, ("activate",))
        self.assertEqual(targets["Private editor"].operations, ("enter",))
        self.assertEqual(targets["Safe field"].description, "")
        self.assertNotIn("PRIVATE_ISLAND_SECRET", repr(snapshot))

    def test_user_edits_invalidate_old_target_and_reinspection_finds_dynamic_editors(
        self,
    ):
        _, view, task, session = self.opened()
        snapshot, target = self.editor(task, session)
        self.value(
            view.page(),
            "editor.textContent='NEW_PRIVATE_DRAFT'; const later=document.createElement('div'); later.contentEditable='true'; later.setAttribute('aria-label','Dynamic editor'); later.textContent='DYNAMIC_PRIVATE'; document.body.append(later); true",
        )
        with self.assertRaises(PageInspectionError) as stale:
            self.act(task, session, snapshot, target, "enter", text="Must not replace")
        self.assertEqual(stale.exception.code, "stale_snapshot")
        current, dynamic = self.editor(task, session, "Dynamic editor")
        self.assertEqual(dynamic.operations, ("enter",))
        self.assertNotIn("NEW_PRIVATE_DRAFT", repr(current))
        self.assertNotIn("DYNAMIC_PRIVATE", repr(current))

    def test_shadow_editors_remain_value_free_and_unsupported_instead_of_disappearing(
        self,
    ):
        _, view, task, session = self.opened('<div id="component"></div>')
        self.value(
            view.page(),
            "component.attachShadow({mode:'open'}).innerHTML='<div contenteditable aria-label=\"Component editor\">SHADOW_PRIVATE_DRAFT<button>INNER_PRIVATE_BUTTON</button></div>'; true",
        )
        snapshot, target = self.editor(task, session, "Component editor")
        self.assertEqual(target.operations, ())
        self.assertIn("shadow_control_actions_unavailable", target.constraints)
        self.assertNotIn("SHADOW_PRIVATE_DRAFT", repr(snapshot))
        self.assertNotIn("INNER_PRIVATE_BUTTON", repr(snapshot))
        self.assertTrue(snapshot.guidance)

    def test_focus_redirect_does_not_type_into_another_control(self):
        _, view, task, session = self.opened()
        self.script(
            view.page(),
            "document.getElementById('editor').focus = ()=>document.getElementById('field').focus(); true",
        )
        snapshot, target = self.editor(task, session)
        with self.assertRaises(PageInspectionError) as failure:
            self.act(task, session, snapshot, target, "enter", text="Must not escape")
        self.assertEqual(failure.exception.code, "focus_unavailable")
        self.assertEqual(
            self.value(view.page(), "document.getElementById('field').value"),
            "PRIVATE_VALUE",
        )

    def test_child_frame_editor_is_observed_without_enabling_untested_frame_input(self):
        _, view, task, session = self.opened(
            """<iframe id="child" srcdoc="&lt;div contenteditable aria-label='Frame editor'&gt;FRAME_PRIVATE_DRAFT&lt;/div&gt;"></iframe>"""
        )
        self.wait_for(
            lambda: self.value(
                view.page(), "child.contentDocument.readyState === 'complete'"
            )
        )
        snapshot = self.inspect(task, session)
        target = next(
            t for f in snapshot.frames for t in f.targets if t.label == "Frame editor"
        )
        self.assertEqual(target.operations, ())
        self.assertIn("editable_control_actions_unavailable", target.constraints)
        self.assertNotIn("FRAME_PRIVATE_DRAFT", repr(snapshot))
        with self.assertRaises(PageInspectionError):
            self.act(task, session, snapshot, target, "enter", text="Not dispatched")


class RestrictedEditorTests(unittest.TestCase):
    setUpClass = classmethod(inspection.PageInspectionTests.setUpClass.__func__)
    tearDownClass = classmethod(inspection.PageInspectionTests.tearDownClass.__func__)
    setUp = inspection.PageInspectionTests.setUp
    tearDown = inspection.PageInspectionTests.tearDown
    wait_for = inspection.PageInspectionTests.wait_for
    command = inspection.PageInspectionTests.command
    request = inspection.PageInspectionTests.request
    session = inspection.PageInspectionTests.session
    load = inspection.PageInspectionTests.load
    inspect = inspection.PageInspectionTests.inspect

    def test_editor_is_observed_but_restricted_page_does_not_gain_live_actions(self):
        task, _, session, page = self.session(
            actions=["navigate", "read", "inspect", "edit"]
        )
        self.load(
            session,
            page,
            '<div contenteditable aria-label="Editor">RESTRICTED_SECRET<button>Nested private</button></div>',
        )
        snapshot = self.inspect(task, session)
        target = snapshot.frames[0].targets[0]
        self.assertEqual(target.kind, "editable")
        self.assertEqual(target.operations, ())
        self.assertNotIn("RESTRICTED_SECRET", repr(snapshot))
        self.assertNotIn("Nested private", repr(snapshot))
        with self.assertRaises(PageInspectionError):
            self.command(
                "page.act",
                task_id=task,
                session_id=session,
                snapshot_id=snapshot.snapshot_id,
                target_id=target.target_id,
                operation="enter",
                text="Not authorized live entry",
            )

    def test_read_permission_is_still_required_for_editor_metadata(self):
        task, _, session, page = self.session(read=False)
        self.load(
            session,
            page,
            '<div contenteditable aria-label="Private editor">SECRET</div>',
        )
        with self.assertRaises(TaskPermissionError):
            self.inspect(task, session)
