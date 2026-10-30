"""Accessible roles and states, and waiting for content that changes later."""

import unittest

import test_live_tabs as live
import test_task_profiles as fixtures
from PyQt6.QtCore import QUrl

from browser_window import BrowserWindow

HTML = """<html><title>Semantics fixture</title><body>
<h1>Semantics</h1>
<input type="search" aria-label="Find">
<input type="text" list="cities" aria-label="City"><datalist id="cities"></datalist>
<input type="number" aria-label="Count" value="3">
<input type="range" aria-label="Volume" value="40">
<select aria-label="Size"><option>S</option><option>M</option></select>
<select aria-label="Colours" multiple><option>Red</option></select>
<a href="/next">Next page</a>
<div role="checkbox" aria-checked="mixed" aria-label="All items" tabindex="0"></div>
<div role="slider" aria-label="Zoom" aria-valuenow="75" aria-valuetext="75 percent"></div>
<button type="button" aria-haspopup="menu" aria-expanded="false">Options</button>
<ul role="tree"><li role="treeitem" aria-selected="true">Inbox</li></ul>
<div id="results" aria-busy="true"><button type="button">Load</button></div>
</body></html>"""


class PageSemanticsTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TaskProfileTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TaskProfileTests.tearDownClass.__func__)
    setUp = fixtures.TaskProfileTests.setUp
    tearDown = fixtures.TaskProfileTests.tearDown
    wait_for = fixtures.TaskProfileTests.wait_for
    command = fixtures.TaskProfileTests.command
    task = live.LiveTabsTests.task
    share = live.LiveTabsTests.share
    value = live.LiveTabsTests.value
    inspect = live.LiveTabsTests.inspect

    def window(self):
        window = BrowserWindow()
        self.windows.append(window)
        window._switch_to_profile("work")
        window.show()
        self.page_fixtures["/semantics"] = HTML.encode()
        view = window.tabs[window.current_tab_index]
        loaded = []
        view.loadFinished.connect(loaded.append)
        view.setUrl(QUrl(self.origin + "/semantics"))
        self.wait_for(lambda: loaded and view.title() == "Semantics fixture")
        return window, view

    def targets(self, snapshot):
        return {t.label: t for t in snapshot.frames[0].targets}

    def test_native_controls_report_their_accessible_roles(self):
        window, _ = self.window()
        task, _, session = self.share(window)
        targets = self.targets(self.inspect(task, session))
        roles = {label: targets[label].role for label in targets}
        self.assertEqual(roles["Find"], "searchbox")
        self.assertEqual(roles["City"], "combobox")
        self.assertEqual(roles["Count"], "spinbutton")
        self.assertEqual(roles["Volume"], "slider")
        self.assertEqual(roles["Size"], "combobox")
        self.assertEqual(roles["Colours"], "listbox")
        self.assertEqual(roles["Next page"], "link")
        self.assertEqual(roles["Options"], "button")

    def test_custom_controls_report_states_and_positions(self):
        window, _ = self.window()
        task, _, session = self.share(window)
        targets = self.targets(self.inspect(task, session))
        self.assertIn("mixed", targets["All items"].states)
        self.assertEqual(targets["All items"].operations, ("activate",))
        self.assertEqual(targets["Zoom"].role, "slider")
        self.assertEqual(targets["Zoom"].value_text, "75 percent")
        self.assertEqual(targets["Zoom"].operations, ())
        self.assertIn("custom_control_unsupported", targets["Zoom"].constraints)
        self.assertEqual(
            set(targets["Options"].states) & {"has_popup", "collapsed"},
            {"has_popup", "collapsed"},
        )
        self.assertIn("selected", targets["Inbox"].states)
        self.assertEqual(targets["Inbox"].operations, ("activate",))
        self.assertIn("busy", targets["Load"].states)
        # Native control values stay omitted.
        self.assertEqual(targets["Volume"].value_text, "")
        self.assertEqual(targets["Count"].value_text, "")

    def wait(self, task, session, timeout_ms=3000):
        return self.command(
            "page.wait",
            task_id=task,
            session_id=session,
            condition="changed",
            timeout_ms=timeout_ms,
        )

    def test_wait_for_change_sees_content_that_arrives_later(self):
        window, view = self.window()
        task, _, session = self.share(window)
        self.inspect(task, session)
        self.value(
            view.page(),
            """setTimeout(() => { const p = document.createElement('p');
            p.textContent = 'Three results found'; document.body.append(p); }, 400);
            true""",
        )
        self.assertEqual(self.wait(task, session).status, "matched")
        snapshot = self.inspect(task, session)
        self.assertIn("Three results found", snapshot.frames[0].text)

    def test_wait_for_change_counts_changes_made_before_the_wait(self):
        window, view = self.window()
        task, _, session = self.share(window)
        self.inspect(task, session)
        # The effect lands before the agent starts waiting.
        self.value(
            view.page(),
            "document.querySelector('[role=checkbox]').setAttribute('aria-checked','true'); true",
        )
        self.assertEqual(self.wait(task, session).status, "matched")

    def test_wait_for_change_times_out_on_a_still_page(self):
        window, _ = self.window()
        task, _, session = self.share(window)
        self.inspect(task, session)
        self.assertEqual(self.wait(task, session, 700).status, "timed_out")

    def test_wait_for_change_rejects_text(self):
        window, _ = self.window()
        task, _, session = self.share(window)
        with self.assertRaises(ValueError):
            self.command(
                "page.wait",
                task_id=task,
                session_id=session,
                condition="changed",
                text="x",
            )


if __name__ == "__main__":
    unittest.main()
