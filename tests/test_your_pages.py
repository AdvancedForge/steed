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
        # Each section answers on its own: wait for all of them.
        self.wait(
            lambda: "allowed" in (text := self.js(tab, TEXT)) and "Loading" not in text
        )
        text = self.js(tab, TEXT)
        self.assertIn("Access → Local pages", text)
        self.assertEqual(text.count("Not allowed yet"), 3)
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

    def test_a_page_asks_on_the_page_and_keeps_what_you_allow_it(self):
        from services.packs import build
        from ui.your_pages import DASHBOARD, YourPagesDialog

        registry = self.window.profile_manager.registry
        pages = registry.start_pages
        profile = self.window.profile_manager.current_profile_name
        pages.save_your_page(profile, "dash", "Dash", DASHBOARD)
        tab = self.window._new_tab(url="steed:dash", return_tab=True)
        bar = self.window.page_access_bar
        self.wait(lambda: bar.isVisible())
        self.assertIn("steed:dash wants to see your watches", bar.label.text())
        bar.grant()
        self.assertFalse(bar.isVisible())
        self.assertEqual(pages.page_allowed(profile, "dash"), {"steed"})
        self.wait(lambda: "Summarize this page" in self.js(tab, TEXT))
        # Only this page: Access → Local pages is unchanged.
        self.assertEqual(
            access_levels.values(self.window.config, "pages")["steed"], "never"
        )
        # Editing keeps it; packs never carry it; a new page of that name starts over.
        pages.save_your_page(profile, "dash", "Dash 2", DASHBOARD, replacing="dash")
        self.assertEqual(pages.page_allowed(profile, "dash"), {"steed"})
        pack = build(registry, profile, pages=["dash"])
        self.assertNotIn("allowed", pack["pages"][0])
        dialog = YourPagesDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        self.assertIn("allowed: see your watches", dialog.list.item(0).text())
        dialog.list.setCurrentRow(0)
        dialog.take_back()
        self.assertEqual(pages.page_allowed(profile, "dash"), set())
        pages.allow_page(profile, "dash", "steed")
        pages.remove_your_page(profile, "dash")
        pages.save_your_page(profile, "dash", "Other", DASHBOARD)
        self.assertEqual(pages.page_allowed(profile, "dash"), set())

    def test_no_bar_when_you_chose_never_or_said_not_now(self):
        from ui.your_pages import DASHBOARD

        pages = self.window.profile_manager.registry.start_pages
        profile = self.window.profile_manager.current_profile_name
        pages.save_your_page(profile, "dash", "Dash", DASHBOARD)
        bar = self.window.page_access_bar
        tab = self.window._new_tab(url="steed:dash", return_tab=True)
        self.wait(lambda: bar.isVisible())
        bar.decline()
        tab.reload()
        self.wait(lambda: "allowed" in self.js(tab, TEXT))
        self.assertFalse(bar.isVisible())  # Not again this session.
        bar.declined.clear()
        access_levels.set_policy(self.window.config, "pages", "steed", "never")
        tab.reload()
        self.wait(lambda: "allowed" in self.js(tab, TEXT))
        self.assertFalse(bar.isVisible())  # You chose Never yourself.

    def test_every_template_shows_its_data_once_allowed(self):
        from ui.your_pages import TEMPLATES

        registry = self.window.profile_manager.registry
        profile = self.window.profile_manager.current_profile_name
        for policy in ("steed", "bookmarks"):
            access_levels.set_policy(self.window.config, "pages", policy, "always")
        registry.start_pages.add_bookmark(
            profile, "https://kettles.example/", "Kettles"
        )
        shown = {}
        for label, name, title, html in TEMPLATES:
            with self.subTest(template=label):
                registry.start_pages.save_your_page(profile, name, title, html)
                tab = self.window._new_tab(url="steed:" + name, return_tab=True)
                self.wait(lambda tab=tab, title=title: tab.title() == title)
                self.wait(lambda tab=tab: "Loading" not in self.js(tab, TEXT))
                shown[name] = self.js(tab, TEXT)
                self.assertNotIn("Not allowed", shown[name])
        self.assertIn("Kettles", shown["links"])
        self.assertIn("No watched pages yet", shown["changes"])


if __name__ == "__main__":
    unittest.main()
