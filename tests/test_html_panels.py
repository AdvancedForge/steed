"""Real HTML panel surfaces preserve explicit sends and source lifetimes."""

import json
import unittest

import test_data_panel_inputs as inputs
import test_html_menus as html
import test_panel_accounts as accounts
import test_panel_library as library
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog


class HtmlPanelTests(unittest.TestCase):
    setUpClass = classmethod(library.LibraryPanelTests.setUpClass.__func__)
    tearDownClass = classmethod(library.LibraryPanelTests.tearDownClass.__func__)
    setUp = library.LibraryPanelTests.setUp
    tearDown = library.LibraryPanelTests.tearDown
    command = library.LibraryPanelTests.command
    request = library.LibraryPanelTests.request
    wait_for = library.LibraryPanelTests.wait_for
    task = library.LibraryPanelTests.task
    window = library.LibraryPanelTests.window
    artifact = library.LibraryPanelTests.artifact
    source = library.LibraryPanelTests.source
    prepare = library.LibraryPanelTests.prepare
    access = library.LibraryPanelTests.access
    ready = html.HtmlMenuTests.ready
    value = html.HtmlMenuTests.value
    control = html.HtmlMenuTests.control
    click = html.HtmlMenuTests.click

    def contains(self, host, text):
        return self.value(
            host,
            "[...document.querySelectorAll('textarea')].some(e => e.value.includes("
            + json.dumps(text)
            + ")) || document.body.textContent.includes("
            + json.dumps(text)
            + ")",
        )

    def surfaces(self):
        window = self.window()
        window._toggle_data_panels(True)
        dock = window.data_panels_dock
        tab = window._new_tab(url="steed:panels", return_tab=True)
        hosts = (dock.presentation, tab.workspace_controller)
        for host in hosts:
            self.ready(host)
            self.assertFalse(QDialog.isVisible(host.source))
            self.assertTrue(host.surface.profile.isOffTheRecord())
        return window, dock, tab, hosts

    def test_tab_and_sidebar_clear_linked_contents_before_repainting_revocation(self):
        self.prepare(content=b"<b>SYNTHETIC_LINKED_NOTES</b>")
        window, dock, tab, hosts = self.surfaces()
        for host in hosts:
            self.assertTrue(self.contains(host, "<b>SYNTHETIC_LINKED_NOTES</b>"))
            self.assertEqual(
                self.value(host, "document.querySelectorAll('b').length"), 0
            )
        self.access(False)
        for host in hosts:
            self.assertIs(host.surface.stack.currentWidget(), host.surface.fallback)
            self.assertNotIn("SYNTHETIC_LINKED_NOTES", str(host.surface._state))
        for host in hosts:
            self.ready(host)
            self.assertFalse(self.contains(host, "SYNTHETIC_LINKED_NOTES"))
        self.access(True)
        for host in hosts:
            self.ready(host)
            self.wait_for(
                lambda host=host: self.contains(host, "SYNTHETIC_LINKED_NOTES")
            )
        window._close_tab(window.tabs.index(tab))
        self.assertTrue(hosts[1].surface.disposed)
        self.assertFalse(dock.presentation.surface.disposed)
        window._switch_to_profile("search")
        self.assertFalse(dock.isVisible())
        self.assertNotIn("SYNTHETIC_LINKED_NOTES", str(hosts[0].surface._state))

    def test_sidebar_close_reopens_same_controller_and_full_page_button_works(self):
        self.prepare()
        window = self.window()
        window._toggle_data_panels(True)
        dock = window.data_panels_dock
        host = dock.presentation
        self.ready(host)
        self.value(host, "document.getElementById('close').click(); true")
        self.wait_for(lambda: not dock.isVisible())
        self.assertFalse(host.closed)
        window._toggle_data_panels(True)
        self.ready(host)
        # Escape from the native reload fallback must hide, not dispose, the dock.
        QTest.keyClick(host, Qt.Key.Key_Escape)
        self.assertFalse(dock.isVisible())
        self.assertFalse(host.closed)
        window._toggle_data_panels(True)
        self.ready(host)
        host.close()
        self.assertFalse(dock.isVisible())
        self.assertFalse(host.closed)
        window._toggle_data_panels(True)
        self.ready(host)
        self.assertTrue(host.isVisible())
        self.click(host, dock.open_page)
        self.wait_for(lambda: any(t.workspace_name == "panels" for t in window.tabs))
        window.close()
        self.assertTrue(host.surface.disposed)

    def test_private_panel_page_never_reads_saved_panels(self):
        from unittest.mock import patch

        window = self.window("search")
        with patch.object(self.service.data_panels, "for_profile") as read:
            tab = window._new_tab(url="steed:panels", return_tab=True)
            self.ready(tab.workspace_controller)
            self.assertTrue(self.contains(tab.workspace_controller, "regular profile"))
            read.assert_not_called()


class HtmlPanelFormTests(unittest.TestCase):
    setUpClass = classmethod(inputs.PanelInputTests.setUpClass.__func__)
    tearDownClass = classmethod(inputs.PanelInputTests.tearDownClass.__func__)
    setUp = inputs.PanelInputTests.setUp
    tearDown = inputs.PanelInputTests.tearDown
    command = inputs.PanelInputTests.command
    request = inputs.PanelInputTests.request
    wait_for = inputs.PanelInputTests.wait_for
    task = inputs.PanelInputTests.task
    artifact = inputs.PanelInputTests.artifact
    window = inputs.PanelInputTests.window
    approve = inputs.PanelInputTests.approve
    proposal = inputs.PanelInputTests.proposal
    enabled = inputs.PanelInputTests.enabled
    ready = html.HtmlMenuTests.ready
    value = html.HtmlMenuTests.value
    control = html.HtmlMenuTests.control
    click = html.HtmlMenuTests.click

    def test_background_draft_update_preserves_html_input_value_and_focus(self):
        task = self.task()
        artifact = self.artifact(task)
        self.approve(self.proposal(task, artifact))
        self.service.external_connection(task.task_id, True)
        window = self.window()
        window._toggle_data_panels(True)
        host = window.data_panels_dock.presentation
        self.ready(host)
        form = host.source.scroll.widget().displays["preferences"]
        token = self.control(host, form.inputs["focus"])
        self.value(
            host,
            f"const e=document.querySelector('[data-control=\"{token}\"]'); "
            'e.focus(); e.value="UNSENT_HTML_VALUE"; e.dispatchEvent(new Event("input")); true',
        )
        self.wait_for(lambda: form.inputs["focus"].text() == "UNSENT_HTML_VALUE")
        replacement = self.artifact(task, "REPLACEMENT_SUMMARY")
        self.command(
            "panel.publish",
            task_id=task.task_id,
            request_id="inputs",
            update_id="update",
            expected_revision=0,
            bindings=[{"id": "summary", "artifact_id": replacement.artifact_id}],
        )
        self.ready(host)
        self.assertEqual(
            self.value(host, "document.activeElement.value"), "UNSENT_HTML_VALUE"
        )
        self.assertIs(host.source.scroll.widget().displays["preferences"], form)
        self.assertEqual(self.service.messages.read(task.task_id)["messages"], [])

    def test_html_form_requires_preview_and_explicit_send_and_does_not_replay(self):
        task, _ = self.enabled()
        window = self.window()
        tab = window._new_tab(url="steed:panels", return_tab=True)
        host = tab.workspace_controller
        self.ready(host)
        form = host.source.scroll.widget().displays["preferences"]
        token = self.control(host, form.inputs["focus"])
        self.value(
            host,
            f"const e=document.querySelector('[data-control=\"{token}\"]'); "
            'e.focus(); e.value="HTML_FORM_VALUE"; e.dispatchEvent(new Event("input")); true',
        )
        self.wait_for(lambda: form.inputs["focus"].text() == "HTML_FORM_VALUE")
        self.click(host, form.send)
        self.wait_for(lambda: form.preview is not None)
        preview = form.preview.presentation_host()
        self.ready(preview)
        self.assertEqual(self.service.messages.read(task.task_id)["messages"], [])
        self.assertIn("HTML_FORM_VALUE", str(preview.surface._state))
        self.click(preview, form.preview_send)
        self.wait_for(lambda: form.preview is None)
        self.assertEqual(len(self.service.messages.read(task.task_id)["messages"]), 1)
        host.surface.reload()
        self.ready(host)
        self.assertEqual(len(self.service.messages.read(task.task_id)["messages"]), 1)


class HtmlPanelAccountTests(unittest.TestCase):
    setUpClass = classmethod(accounts.AccountPanelTests.setUpClass.__func__)
    tearDownClass = classmethod(accounts.AccountPanelTests.tearDownClass.__func__)
    setUp = accounts.AccountPanelTests.setUp
    tearDown = accounts.AccountPanelTests.tearDown
    command = accounts.AccountPanelTests.command
    wait_for = accounts.AccountPanelTests.wait_for
    window = accounts.AccountPanelTests.window
    propose = accounts.AccountPanelTests.propose
    ready = html.HtmlMenuTests.ready
    value = html.HtmlMenuTests.value
    control = html.HtmlMenuTests.control
    click = html.HtmlMenuTests.click

    def test_only_explicit_html_refresh_fetches_and_revocation_clears_account_data(
        self,
    ):
        self.http.body["body"] = "SYNTHETIC_NOTE"
        self.propose()
        window = self.window()
        before = len(self.http.calls)
        tab = window._new_tab(url="steed:panels", return_tab=True)
        host = tab.workspace_controller
        self.ready(host)
        window._toggle_data_panels(True)
        self.ready(window.data_panels_dock.presentation)
        self.assertEqual(len(self.http.calls), before)
        self.click(host, host.source.refresh_button)
        self.wait_for(lambda: len(self.http.calls) == before + 2)
        self.wait_for(lambda: not self.service.data_panels.account_reads.inflight)
        self.ready(host)
        self.assertIn("SYNTHETIC_NOTE", str(host.surface._state))
        host.surface.reload()
        self.ready(host)
        self.assertEqual(len(self.http.calls), before + 2)
        self.service.set_connector_access(self.account.connector, "read", False)
        self.assertNotIn("SYNTHETIC_NOTE", str(host.surface._state))
        self.ready(host)
        self.assertFalse(HtmlPanelTests.contains(self, host, "SYNTHETIC_NOTE"))
