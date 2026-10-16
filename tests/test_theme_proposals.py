"""External agents can propose appearance, never choose their own approval."""

import json
import unittest

import test_start_page_proposals as fixtures
from PyQt6 import sip

from start_page_format import parse_page, state_digest
from ui.start_page_review import StartPageReviewDialog


class ThemeProposalTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.PageProposalTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.PageProposalTests.tearDownClass.__func__)
    setUp = fixtures.PageProposalTests.setUp
    tearDown = fixtures.PageProposalTests.tearDown
    wait_for = fixtures.PageProposalTests.wait_for
    command = fixtures.PageProposalTests.command
    propose = fixtures.PageProposalTests.propose

    def theme(self, **patch):
        return json.dumps(
            {
                "version": 1,
                "name": "Agent theme",
                "page": "style",
                "mode": "theme",
                "style": patch or {"colors": {"bg": "#102030", "ink": "#ffffff"}},
            }
        )

    def review(self):
        task, receipt = self.propose(source=self.theme())
        dialog = StartPageReviewDialog(self.service, receipt)
        dialog.profile.setCurrentText("work")
        self.addCleanup(
            lambda: (
                dialog.reject()
                if not sip.isdeleted(dialog) and dialog.result() == 0
                else None
            )
        )
        return task, receipt, dialog

    def test_capabilities_publish_guide_without_reading_existing_style(self):
        task = self.command("task.create", objective="Customize my colors")
        styles = self.registry.page_style
        value = styles.get("work")
        styles.save("work", {**value, "css": "/* PRIVATE_CSS */"})
        caps = self.command("start_page.capabilities", task_id=task.task_id)
        self.assertIn("--surface", caps["style_guide"]["colors"])
        self.assertEqual(caps["style_example"]["page"], "style")
        self.assertNotIn("PRIVATE_CSS", json.dumps(caps))
        self.assertEqual(self.service.store.grants(task.task_id), [])

    def test_owner_review_applies_only_selected_profile_and_does_not_replay(self):
        task, receipt, review = self.review()
        before = self.registry.page_style.get("global")
        self.assertNotIn("bg", self.registry.page_style.get("work")["colors"])
        review.apply()
        self.assertEqual(
            self.service.start_pages.get(task.task_id, receipt.request_id).state,
            "applied",
        )
        self.assertEqual(
            self.registry.page_style.get("work")["colors"]["bg"], "#102030"
        )
        self.assertEqual(self.registry.page_style.get("global"), before)
        self.assertEqual(self.service.store.grants(task.task_id), [])
        with self.assertRaises(ValueError):
            self.service.start_pages.resolve(
                task.task_id,
                receipt.request_id,
                approve=True,
                profile="work",
                profile_key=review.profile_key,
                base=state_digest(self.registry.page_style.get("work")),
            )

    def test_all_profiles_requires_visible_owner_checkbox(self):
        _, _, review = self.review()
        self.assertFalse(review.all_profiles.isChecked())
        review.all_profiles.setChecked(True)
        review.apply()
        self.assertEqual(
            self.registry.page_style.get("global")["colors"]["bg"], "#102030"
        )

    def test_stale_style_blocks_review(self):
        task, receipt, review = self.review()
        styles = self.registry.page_style
        styles.save("work", {**styles.get("work"), "theme": "dark"})
        review.apply()
        self.assertEqual(
            self.service.start_pages.get(task.task_id, receipt.request_id).state,
            "awaiting_approval",
        )
        self.assertFalse(review.apply_button.isEnabled())
        self.assertNotIn("bg", styles.get("work")["colors"])

    def test_style_patch_cannot_carry_native_or_unknown_configuration(self):
        for patch in (
            {"agent_access": True},
            {"colors": {"cookie_value": "#ffffff"}},
            {"colors": {"bg": "url(https://example.test)"}},
            {"css": "</style>"},
            {"all_profiles": True},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                parse_page(self.theme(**patch))
