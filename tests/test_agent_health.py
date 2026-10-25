"""Connection/read facts, offline drafts and sanitized failure recovery guidance."""

import unittest

import test_agent_gateway as gateway
import test_html_agents as html

from ui.agent_sidebar import AgentSidebar


class AgentHealthTests(unittest.TestCase):
    setUpClass = classmethod(html.HtmlAgentTests.setUpClass.__func__)
    tearDownClass = classmethod(html.HtmlAgentTests.tearDownClass.__func__)
    setUp = html.HtmlAgentTests.setUp
    tearDown = html.HtmlAgentTests.tearDown
    wait_for = html.HtmlAgentTests.wait_for
    command = html.HtmlAgentTests.command
    sidebar = html.HtmlAgentTests.sidebar
    connect_task = html.HtmlAgentTests.connect_task
    loaded = html.HtmlAgentTests.loaded
    value = html.HtmlAgentTests.value
    input = html.HtmlAgentTests.input
    click = html.HtmlAgentTests.click

    def test_read_is_not_working_and_offline_drafts_survive_reconnection_without_send(
        self,
    ):
        _, sidebar = self.loaded()
        task = self.connect_task(sidebar)
        self.service.send_message(task, "One explicit message")
        self.assertEqual(sidebar.state["messages"][0]["delivery"], "Waiting to be read")
        self.command("conversation.read", task_id=task)
        self.assertEqual(sidebar.state["messages"][0]["delivery"], "Read by agent")
        self.assertFalse(sidebar.state["activity"]["busy"])
        self.service.external_connection(task, False)
        self.wait_for(
            lambda: self.value(
                sidebar,
                "!composer.disabled && document.getElementById('send').disabled",
            )
        )
        self.assertEqual(sidebar.state["connection"]["state"], "disconnected")
        self.input(sidebar, "Offline draft")
        sidebar.surface.reload()
        self.wait_for(lambda: sidebar.surface.ready)
        self.wait_for(lambda: self.value(sidebar, "composer.value === 'Offline draft'"))
        self.service.external_connection(task, True)
        self.wait_for(
            lambda: self.value(sidebar, "!document.getElementById('send').disabled")
        )
        self.assertEqual(len(self.service.messages.read(task)["messages"]), 1)
        self.assertEqual(sidebar.draft, "Offline draft")
        self.service.cancel_task(task)
        self.assertTrue(sidebar.state["can_draft"])
        self.assertFalse(sidebar.state["active"])

    def test_pending_approval_is_visible_without_claiming_generic_agent_work(self):
        _, sidebar = self.loaded()
        task = self.connect_task(sidebar)
        self.command("grant.request", task_id=task, scope={"actions": ["artifact"]})
        self.assertEqual(sidebar.state["activity"]["title"], "Approval requested")
        self.assertFalse(sidebar.state["activity"]["busy"])
        self.assertEqual(sidebar.state["connection"]["label"], "Connected")

    def test_tool_failure_is_task_scoped_sanitized_and_dismissible(self):
        _, sidebar = self.loaded()
        first = self.connect_task(sidebar, "First")
        second = self.connect_task(sidebar, "Second")
        self.service.store.record_event(
            first,
            "client.rejected",
            {"command": "page.click", "code": "native_input_timeout"},
        )
        self.service.changed.emit()
        self.assertIsNone(sidebar.state["failure"])
        sidebar.select_task(first)
        self.wait_for(
            lambda: self.value(
                sidebar,
                "document.getElementById('failure-guidance').textContent.includes('may already have reached')",
            )
        )
        self.assertEqual(sidebar.state["failure"]["action"], "receipts")
        self.value(
            sidebar,
            "document.querySelector('[data-action=dismiss_failure]').click(); true",
        )
        self.wait_for(lambda: sidebar.state["failure"] is None)
        self.service.store.record_event(
            first,
            "client.rejected",
            {"command": "PRIVATE_PAYLOAD", "code": "PRIVATE_EXCEPTION"},
        )
        self.service.changed.emit()
        self.assertNotIn("PRIVATE_", str(sidebar.state))
        self.assertEqual(sidebar.state["failure"]["code"], "operation_failed")
        sidebar.select_task(second)
        self.assertIsNone(sidebar.state["failure"])


class GatewayHealthTests(unittest.TestCase):
    setUpClass = classmethod(gateway.AgentGatewayTests.setUpClass.__func__)
    tearDownClass = classmethod(gateway.AgentGatewayTests.tearDownClass.__func__)
    setUp = gateway.AgentGatewayTests.setUp
    wait_for = gateway.AgentGatewayTests.wait_for
    connect = gateway.AgentGatewayTests.connect
    receive = gateway.AgentGatewayTests.receive
    call = gateway.AgentGatewayTests.call
    start = gateway.AgentGatewayTests.start

    def tearDown(self):
        if hasattr(self, "sidebar"):
            self.sidebar.detach()
            self.sidebar.close()
        gateway.AgentGatewayTests.tearDown(self)

    def test_real_validation_and_execution_failures_notify_selected_conversation(self):
        socket = self.connect()
        task = self.start(socket)
        self.sidebar = AgentSidebar(self.service)
        self.sidebar.select_task(task)
        response = self.call(
            socket, "grant.request", scope={"actions": ["unsupported"]}
        )
        self.assertEqual(response["error"]["code"], "invalid_scope")
        self.assertEqual(self.sidebar.state["failure"]["code"], "invalid_scope")
        response = self.call(socket, "session.open")
        self.assertEqual(response["error"]["code"], "approval_required")
        self.assertEqual(self.sidebar.state["failure"]["code"], "approval_required")
        self.assertEqual(self.service.store.grants(task), [])
