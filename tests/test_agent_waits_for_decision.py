"""Asking for access waits for the person's answer, so the agent carries on
when they approve instead of ending its turn with the request still open."""

import unittest
from unittest.mock import patch

import agent_mcp
from agent_mcp import await_decision


class Answers:
    """A connection whose status calls report requested, then the answer."""

    def __init__(self, tool, answers):
        self.tool, self.answers, self.calls = tool, list(answers), []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        assert name == self.tool
        return {
            "result": self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        }


class WaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        patcher = patch.object(agent_mcp, "DECISION_POLL", 0.01)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_grant_request_returns_the_decision(self):
        requested = {"grant_id": "g1", "status": "requested"}
        connection = Answers(
            "steed_grant_list",
            [[requested], [requested], [{"grant_id": "g1", "status": "approved"}]],
        )
        result = await await_decision(
            connection, "steed_grant_request", {"result": requested}, wait=5
        )
        self.assertEqual(result["result"]["status"], "approved")
        self.assertEqual(len(connection.calls), 3)

    async def test_tab_control_waits_too_and_says_so_when_undecided(self):
        requested = {"request_id": "r1", "state": "requested"}
        connection = Answers("steed_browser_control_status", [requested])
        result = await await_decision(
            connection,
            "steed_browser_request_control",
            {"result": requested},
            wait=0.1,
        )
        self.assertEqual(result["result"]["state"], "requested")
        self.assertIn("Do not end your turn", result["result"]["waiting"])
        self.assertIn("steed_browser_control_status", result["result"]["waiting"])
        self.assertEqual(connection.calls[0][1], {"request_id": "r1"})

    async def test_asking_the_person_waits_until_they_hand_back(self):
        waiting = {"request_id": "h1", "state": "waiting_for_person"}
        connection = Answers(
            "steed_browser_ask_person_status",
            [waiting, {"request_id": "h1", "state": "done"}],
        )
        result = await await_decision(
            connection, "steed_browser_ask_person", {"result": waiting}, wait=5
        )
        self.assertEqual(result["result"]["state"], "done")
        self.assertEqual(connection.calls[0][1], {"request_id": "h1"})

    async def test_other_calls_and_settled_requests_return_at_once(self):
        connection = Answers("never", [None])
        for name, value in (
            ("steed_page_read", {"text": "x"}),
            ("steed_grant_request", {"grant_id": "g", "status": "approved"}),
            ("steed_browser_request_control", {"request_id": "r", "state": "denied"}),
        ):
            result = await await_decision(connection, name, {"result": value}, wait=5)
            self.assertEqual(result, {"result": value})
        self.assertEqual(connection.calls, [])


if __name__ == "__main__":
    unittest.main()
