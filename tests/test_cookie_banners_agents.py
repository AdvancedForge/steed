"""Agents' own pages follow Settings → Cookie banners → agents' pages."""

import unittest

import test_cookie_banners as banners
import test_live_tabs as fixtures

from browser import cookie_banners


class AgentPageTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.LiveTabsTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.LiveTabsTests.tearDownClass.__func__)
    setUp = fixtures.LiveTabsTests.setUp
    tearDown = fixtures.LiveTabsTests.tearDown
    wait_for = fixtures.LiveTabsTests.wait_for
    command = fixtures.LiveTabsTests.command
    task = fixtures.LiveTabsTests.task

    def open(self, agents):
        config = self.service.backend.browser_config
        config.set("cookie_banners", "leave")
        config.set("cookie_banners_agents", agents)
        self.page_fixtures["/consent"] = banners.COOKIEBOT.encode()
        task = self.task()
        grant = fixtures.fixtures.TaskProfileTests.request(
            self, task, actions=["navigate", "read", "live", "inspect"]
        )
        self.service.approve_grant(task, grant.grant_id)
        session = self.command("session.open", task_id=task, mode="live").session_id
        page = self.service.backend._sessions[session].page
        self.command(
            "session.navigate",
            task_id=task,
            session_id=session,
            url=self.origin + "/consent",
        )
        return page

    def test_agents_pages_reject_even_when_you_answer_yourself(self):
        page = self.open("reject")
        self.assertEqual(page.cookie_banner_settings()[0], "reject")
        self.wait_for(lambda: page.title() == "rejected")
        self.assertEqual(page.cookie_banner["answer"], "reject")

    def test_same_as_yours_leaves_them(self):
        page = self.open("same")
        self.assertEqual(page.cookie_banner_settings()[0], "leave")
        installed = [s.name() for s in page.profile().scripts().toList()]
        self.assertNotIn(cookie_banners.SCRIPT_NAME, installed)


if __name__ == "__main__":
    unittest.main()
