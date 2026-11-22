"""Your own steed:<name> pages: dashboards and tools with Steed's page API."""

import unittest
from unittest.mock import patch

import test_writing_help as fixture
from PyQt6.QtCore import QUrl

from browser import access_levels, webengine
from services.page_api import api_endpoint
from services.start_pages import clean_page_name, display_url, internal_url

PAGE = """<h1 id=title>Board</h1><script>
window.results = {};
for (const name of ["skills", "watches", "routines"])
  fetch("/api/" + name).then(r => r.json()).then(data => window.results[name] = data);
localStorage.setItem("kept", "yes");
</script>"""


TEXT = "document.body ? document.body.innerText : ''"


class NameTests(unittest.TestCase):
    def test_names_addresses_and_steeds_own(self):
        self.assertEqual(clean_page_name(" steed:Dashboard "), "dashboard")
        for bad in (
            "settings",
            "newtab",
            "app-123456789012",
            "9lives",
            "a b",
            "x" * 33,
        ):
            with self.assertRaises(ValueError, msg=bad):
                clean_page_name(bad)
        self.assertEqual(
            internal_url("steed:dashboard").toString(), "steed://dashboard"
        )
        self.assertEqual(internal_url("steed:settings").toString(), "steed://settings/")
        self.assertEqual(display_url(QUrl("steed://dashboard/")), "steed:dashboard")
        # Steed-only endpoints are for your pages, not New Tab or panel apps.
        self.assertEqual(
            api_endpoint(QUrl("steed://dashboard/api/run-skill")), "run-skill"
        )
        self.assertIsNone(api_endpoint(QUrl("steed://newtab/api/run-skill")))
        self.assertEqual(api_endpoint(QUrl("steed://newtab/api/tabs")), "tabs")
        self.assertIsNone(api_endpoint(QUrl("steed://settings/api/tabs")))


class YourPageTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WritingHelpTests.setUpClass.__func__)
    setUp = fixture.WritingHelpTests.setUp
    tearDown = fixture.WritingHelpTests.tearDown
    wait = fixture.WritingHelpTests.wait

    def js(self, tab, script):
        found = []
        webengine.run_script(
            tab.page(), script, found.append, world=webengine.PAGE_WORLD
        )
        self.wait(lambda: found)
        return found[0]

    def test_a_page_of_yours_opens_and_uses_steeds_api(self):
        registry = self.window.profile_manager.registry
        profile = self.window.profile_manager.current_profile_name
        registry.start_pages.save_your_page(profile, "board", "Board", PAGE)
        with self.assertRaises(ValueError):
            registry.start_pages.save_your_page(profile, "board", "Again", PAGE)
        access_levels.set_policy(self.window.config, "pages", "steed", "always")
        tab = self.window._new_tab(url="steed:board", return_tab=True)
        self.wait(lambda: tab.title() == "Board")
        self.wait(lambda: self.js(tab, "Object.keys(window.results).length") == 3)
        skills = self.js(tab, "JSON.stringify(window.results.skills)")
        self.assertIn("Summarize this page", skills)
        self.assertEqual(self.js(tab, "localStorage.getItem('kept')"), "yes")
        self.assertEqual(self.window.address_bar.address_input.text(), "steed:board")
        # Running things needs its own permission: never by default.
        denied = self.js(
            tab,
            "fetch('/api/open?url=https://example.com').then(r => r.json())"
            ".then(d => window.opened = d.status || 200); true",
        )
        self.assertTrue(denied)
        self.wait(lambda: self.js(tab, "window.opened || 0") != 0)
        self.assertEqual(self.js(tab, "window.opened"), 403)
        access_levels.set_policy(self.window.config, "pages", "automation", "always")
        before = len(self.window.tabs)
        self.js(tab, "fetch('/api/open?url=https://example.com/'); true")
        self.wait(lambda: len(self.window.tabs) == before + 1)

    def test_the_editor_starts_from_a_template_and_opens_the_page(self):
        from ui.your_pages import YourPageEditor, YourPagesDialog

        editor = YourPageEditor(self.window)
        self.assertEqual(editor.name.text(), "dashboard")
        self.assertIn("/api/watches", editor.html.toPlainText())
        with patch.object(fixture.BrowserWindow, "_new_tab") as opened:
            editor.save()
        opened.assert_called_once_with(url="steed:dashboard")
        dialog = YourPagesDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.list.count(), 1)
        self.assertIn("steed:dashboard", dialog.list.item(0).text())

    def test_the_dashboard_template_explains_a_page_not_allowed(self):
        from ui.your_pages import DASHBOARD

        registry = self.window.profile_manager.registry
        profile = self.window.profile_manager.current_profile_name
        registry.start_pages.save_your_page(profile, "dash", "Dash", DASHBOARD)
        tab = self.window._new_tab(url="steed:dash", return_tab=True)
        self.wait(lambda: tab.title() == "Dash")
        self.wait(lambda: "allowed" in self.js(tab, TEXT))
        text = self.js(tab, TEXT)
        self.assertIn("Access → Local pages", text)
        self.assertNotIn("Loading", text)
        # Allowed, it lists what there is, and says what to do when empty.
        access_levels.set_policy(self.window.config, "pages", "steed", "always")
        tab.reload()
        self.wait(lambda: "Summarize this page" in self.js(tab, TEXT))
        self.assertIn("No automations yet", self.js(tab, TEXT))
        # Your pages start out in Steed's colours.
        self.assertNotEqual(
            self.js(tab, "getComputedStyle(document.body).backgroundColor"),
            "rgba(0, 0, 0, 0)",
        )


if __name__ == "__main__":
    unittest.main()
