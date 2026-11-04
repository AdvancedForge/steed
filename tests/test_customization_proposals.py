"""Agent-proposed user scripts and panel apps: exact review, add-only, user applies."""

import json
import unittest

import test_task_profiles as profiles
from PyQt6.QtWidgets import QPushButton

from services.customization_proposals import EXAMPLES, parse
from services.task_policy import TaskPermissionError
from ui.customization_review import CustomizationReviewDialog

SCRIPT = json.dumps(EXAMPLES["user_script"])
APP = json.dumps(EXAMPLES["panel_app"])


class FormatTests(unittest.TestCase):
    def test_exact_formats_only(self):
        self.assertEqual(parse(SCRIPT).kind, "user_script")
        self.assertEqual(parse(APP).document["name"], "Scratch notes")
        everywhere = {**EXAMPLES["user_script"], "sites": ["*"], "js": "x()"}
        self.assertEqual(parse(json.dumps(everywhere)).document["sites"], ["*"])
        bad = [
            "",
            "[]",
            '{"version":1,"version":1,"kind":"panel_app","name":"a","html":""}',
            json.dumps({**EXAMPLES["user_script"], "version": 2}),
            json.dumps({**EXAMPLES["user_script"], "kind": "extension"}),
            json.dumps({**EXAMPLES["user_script"], "id": "0" * 32}),
            json.dumps({**EXAMPLES["user_script"], "enabled": False}),
            json.dumps({**EXAMPLES["user_script"], "sites": "example.org"}),
            json.dumps({**EXAMPLES["user_script"], "sites": ["not a site"]}),
            json.dumps({**EXAMPLES["user_script"], "css": "", "js": ""}),
            json.dumps({**EXAMPLES["user_script"], "js": 5}),
            json.dumps({**EXAMPLES["panel_app"], "name": ""}),
            json.dumps({**EXAMPLES["panel_app"], "html": "x\x00"}),
            '{"version":1,"kind":"panel_app","name":"a","html":NaN}',
            " " * (320 * 1024 + 1),
        ]
        for source in bad:
            with self.subTest(source=source[:40]), self.assertRaises(ValueError):
                parse(source)


class ProposalTests(unittest.TestCase):
    setUpClass = classmethod(profiles.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(profiles.TaskProfileTests.tearDownClass.__func__)
    setUp = profiles.TaskProfileTests.setUp
    tearDown = profiles.TaskProfileTests.tearDown
    wait_for = profiles.TaskProfileTests.wait_for

    def command(self, name, *, client=None, **arguments):
        future = (client or self.service.broker).submit(name, **arguments)
        self.wait_for(future.done)
        return future.result()

    def propose(self, source=SCRIPT, request="one", task=None):
        task = task or self.command("task.create", objective="Tidy example.org")
        receipt = self.command(
            "customization.propose",
            task_id=task.task_id,
            request_id=request,
            source=source,
        )
        return task, receipt

    def apply(self, task, receipt, profile="work", **overrides):
        arguments = {
            "approve": True,
            "profile": profile,
            "profile_key": self.registry.start_pages.profile_key(profile, create=True),
        }
        return self.service.customizations.resolve(
            task.task_id, receipt.request_id, **{**arguments, **overrides}
        )

    def test_agent_discovers_proposes_and_polls_but_cannot_apply(self):
        task, receipt = self.propose()
        client = self.service.client(task.task_id)
        capabilities = self.command("customization.capabilities", client=client)
        self.assertTrue(capabilities["available"])
        self.assertEqual(capabilities["kinds"], ["user_script", "panel_app"])
        self.assertEqual(self.service.store.grants(task.task_id), [])
        self.assertEqual(
            self.command("customization.status", client=client, request_id="one"),
            receipt,
        )
        self.assertEqual(receipt.state, "awaiting_approval")
        future = client.submit("customization.resolve", request_id="one", approve=True)
        self.app.processEvents()
        with self.assertRaises(TaskPermissionError):
            future.result()
        self.assertEqual(self.service.messages.summary(task.task_id)["pending"], 1)
        # Nothing exists before the user applies it.
        self.assertEqual(self.registry.user_scripts("work").entries, [])
        # Identical retries return the receipt; changed reuse is refused.
        self.assertEqual(self.propose(task=task)[1], receipt)
        with self.assertRaises(ValueError):
            self.propose(APP, task=task)

    def test_applying_adds_new_entries_to_the_chosen_profile_only(self):
        task, script = self.propose()
        self.registry.user_scripts("work").save(
            {"name": "Mine", "sites": "mine.org", "css": "p{}"}
        )
        applied = self.apply(task, script)
        self.assertEqual(applied.state, "applied")
        entries = self.registry.user_scripts("work").entries
        self.assertEqual([e["name"] for e in entries], ["Mine", "Wider reading column"])
        self.assertTrue(entries[1]["enabled"])
        self.assertEqual(self.registry.user_scripts("global").entries, [])
        with self.assertRaises(ValueError):
            self.apply(task, script)
        _, app = self.propose(APP, request="two", task=task)
        self.apply(task, app, profile="global")
        apps = self.registry.start_pages.state("global")["apps"]
        self.assertEqual([a["name"] for a in apps], ["Scratch notes"])
        self.assertEqual(self.registry.start_pages.state("work")["apps"], [])

    def test_private_and_mismatched_profiles_are_refused(self):
        task, receipt = self.propose()
        with self.assertRaises(ValueError):
            self.apply(task, receipt, profile_key="wrong")
        with self.assertRaises(ValueError):
            self.service.customizations.resolve(
                task.task_id,
                receipt.request_id,
                approve=True,
                profile="search",
                profile_key="private",
            )
        self.assertEqual(
            self.service.customizations.get(task.task_id, "one").state,
            "awaiting_approval",
        )
        self.service.customizations.resolve(task.task_id, "one", approve=False)
        self.assertEqual(
            self.service.customizations.get(task.task_id, "one").state, "denied"
        )

    def test_review_shows_code_without_running_it_and_defaults_to_decline(self):
        everywhere = {**EXAMPLES["user_script"], "sites": ["*"], "js": "steal()"}
        task, receipt = self.propose(json.dumps(everywhere))
        dialog = CustomizationReviewDialog(self.service, receipt)
        try:
            dialog.show()
            self.app.processEvents()
            defaults = [
                b.text() for b in dialog.findChildren(QPushButton) if b.isDefault()
            ]
            self.assertEqual(defaults, ["Decline"])
            self.assertIn("steal()", dialog.source.toPlainText())
            texts = " ".join(
                label.text() for label in dialog.findChildren(type(dialog.status))
            )
            self.assertIn("every website", texts)
            self.assertEqual(self.registry.user_scripts("work").entries, [])
            dialog.profile.setCurrentText("work")
            dialog.apply()
            self.assertEqual(len(self.registry.user_scripts("work").entries), 1)
        finally:
            if dialog.isVisible():
                dialog.close()

    def test_ending_the_task_cancels_pending_proposals(self):
        task, receipt = self.propose()
        self.command("task.finish", client=self.service.client(task.task_id))
        self.assertEqual(
            self.service.customizations.get(task.task_id, "one").state, "cancelled"
        )

    def test_tools_are_published_to_agents(self):
        from services.agent_tools import TOOLS

        names = {tool.command for tool in TOOLS}
        for name in (
            "customization.capabilities",
            "customization.propose",
            "customization.status",
        ):
            self.assertIn(name, names)


if __name__ == "__main__":
    unittest.main()
