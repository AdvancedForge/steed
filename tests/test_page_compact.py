"""Agents read pages compactly by default: one line per target."""

import json
import unittest

import test_page_inspection as fixtures

from services.agent_tools import json_value
from services.page_inspector import PageSnapshot

HTML = """<html><title>Compact fixture</title><body>
<h1>Orders</h1><nav aria-label="Main"><a href="/home">Home</a> <a href="/help">Help</a></nav>
<form action="/search" method="get"><input name="q" aria-label="Search orders" required>
<button>Find</button></form>
<input type="checkbox" aria-label="Only open orders" checked>
<a href="https://other.example/partner">Partner site</a>
<p>Three orders are waiting for you to confirm the delivery address.</p>
</body></html>"""


class CompactSnapshotTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.PageInspectionTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.PageInspectionTests.tearDownClass.__func__)
    setUp = fixtures.PageInspectionTests.setUp
    tearDown = fixtures.PageInspectionTests.tearDown
    session = fixtures.PageInspectionTests.session
    command = fixtures.PageInspectionTests.command
    request = fixtures.PageInspectionTests.request
    load = fixtures.PageInspectionTests.load
    wait_for = fixtures.PageInspectionTests.wait_for

    def client(self, task, **arguments):
        future = self.service.client(task).submit("page.inspect", **arguments)
        self.wait_for(future.done)
        return future.result()

    def test_agents_get_target_lines_and_can_ask_for_every_field(self):
        task, _, session, page = self.session(
            actions=["navigate", "inspect", "read", "attach", "edit"]
        )
        self.load(session, page, HTML)
        compact = self.client(task, session_id=session)
        self.assertEqual(compact["detail"], "compact")
        lines = compact["frames"][0]["targets"].splitlines()
        search = next(line for line in lines if '"Search orders"' in line)
        self.assertIn("{enter", search)
        self.assertIn("required", search)
        home = next(line for line in lines if '"Home"' in line)
        self.assertIn("-> /home", home)  # Same-site links are shortened.
        partner = next(line for line in lines if '"Partner site"' in line)
        self.assertIn("-> https://other.example/partner", partner)
        self.assertTrue(
            any(line.startswith("# Orders") for line in compact["frames"][0]["outline"])
        )
        self.assertIn("confirm the delivery address", compact["frames"][0]["text"])
        # A target id read from a compact line works for actions.
        target_id = search.split("]")[0][1:]
        future = self.service.client(task).submit(
            "page.act",
            session_id=session,
            snapshot_id=compact["snapshot_id"],
            target_id=target_id,
            operation="enter",
            text="order 42",
        )
        self.wait_for(future.done)
        self.assertIsNone(future.exception())
        full = self.client(task, session_id=session, detail="full")
        self.assertIsInstance(full, PageSnapshot)
        self.assertLess(
            len(json.dumps(json_value(compact))), len(json.dumps(json_value(full)))
        )

    def test_the_schema_offers_both(self):
        from services.agent_tools import TOOLS_BY_NAME

        schema = TOOLS_BY_NAME["steed_page_inspect"].input_schema
        self.assertEqual(schema["properties"]["detail"]["enum"], ["compact", "full"])


if __name__ == "__main__":
    unittest.main()
