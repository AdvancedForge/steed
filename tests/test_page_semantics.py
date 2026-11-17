"""Accessible roles and states, and waiting for content that changes later."""

import unittest

import test_live_tabs as live
import test_task_profiles as fixtures
from PyQt6.QtCore import QUrl

from browser.browser_window import BrowserWindow
from services.page_inspector import PageInspectionError

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
<div role="slider" tabindex="0" aria-label="Brightness" aria-valuemin="0"
  aria-valuemax="10" aria-valuenow="5"></div>
<div role="spinbutton" tabindex="0" aria-label="Guests" aria-valuenow="2"></div>
<div role="combobox" tabindex="0" aria-label="Country" aria-expanded="false"
  aria-controls="countries" style="padding:6px">Choose</div>
<ul role="listbox" id="countries" hidden>
  <li role="option" style="padding:6px">Norway</li></ul>
<script>
const step = (node, keys, min, max) => node.addEventListener('keydown', event => {
  const delta = keys[event.key]; if (delta === undefined) return;
  const value = Math.min(max, Math.max(min, +node.getAttribute('aria-valuenow') + delta));
  node.setAttribute('aria-valuenow', value); event.preventDefault(); });
step(document.querySelector('[aria-label=Brightness]'), {ArrowRight: 1, ArrowLeft: -1}, 0, 10);
step(document.querySelector('[aria-label=Guests]'), {ArrowUp: 1, ArrowDown: -1}, 1, 8);
const box = document.querySelector('[role=combobox]'), list = document.getElementById('countries');
box.addEventListener('click', () => { list.hidden = false; box.setAttribute('aria-expanded', 'true'); });
list.querySelector('li').addEventListener('click', () => {
  box.textContent = 'Norway'; list.hidden = true; box.setAttribute('aria-expanded', 'false'); });
</script>
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

    def act(self, task, session, snapshot, target, operation, **extra):
        return self.command(
            "page.act",
            task_id=task,
            session_id=session,
            snapshot_id=snapshot.snapshot_id,
            target_id=target.target_id,
            operation=operation,
            **extra,
        )

    def adjust(self, task, session, label, steps):
        snapshot = self.inspect(task, session)
        target = self.targets(snapshot)[label]
        self.assertEqual(target.operations, ("adjust",))
        self.assertIn("keyboard_adjustment", target.constraints)
        return self.act(task, session, snapshot, target, "adjust", steps=steps)

    def test_custom_sliders_and_spin_buttons_move_by_arrow_keys(self):
        window, view = self.window()
        task, _, session = self.share(window)
        result = self.adjust(task, session, "Brightness", 3)
        self.assertEqual((result.status, result.value_text), ("applied", "8"))
        self.assertEqual(result.input_semantics, "chromium_keyboard")
        # The widget clamps; Steed reports what it became.
        result = self.adjust(task, session, "Brightness", -40)
        self.assertEqual(result.value_text, "0")
        result = self.adjust(task, session, "Brightness", -1)
        self.assertEqual((result.status, result.changed), ("no_change", False))
        result = self.adjust(task, session, "Guests", 2)
        self.assertEqual(result.value_text, "4")
        self.assertEqual(
            self.value(
                view.page(),
                "document.querySelector('[aria-label=Guests]').getAttribute('aria-valuenow')",
            ),
            "4",
        )

    def test_adjust_is_refused_for_other_controls_and_bad_steps(self):
        window, _ = self.window()
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        targets = self.targets(snapshot)
        with self.assertRaises(PageInspectionError) as refused:
            self.act(task, session, snapshot, targets["Find"], "adjust", steps=1)
        self.assertEqual(refused.exception.code, "control_unsupported")
        for steps in (0, 101, None):
            with self.assertRaises(ValueError):
                self.act(
                    task,
                    session,
                    snapshot,
                    targets["Brightness"],
                    "adjust",
                    steps=steps,
                )
        with self.assertRaises(ValueError):
            self.act(task, session, snapshot, targets["Load"], "activate", steps=1)

    def test_custom_combobox_opens_and_its_option_is_chosen(self):
        window, view = self.window()
        task, _, session = self.share(window)
        snapshot = self.inspect(task, session)
        box = self.targets(snapshot)["Country"]
        self.assertEqual(box.operations, ("activate",))
        self.assertIn("collapsed", box.states)
        self.act(task, session, snapshot, box, "activate")
        self.wait_for(
            lambda: self.value(
                view.page(), "!document.getElementById('countries').hidden"
            )
        )
        snapshot = self.inspect(task, session)
        option = next(t for t in snapshot.frames[0].targets if t.role == "option")
        self.assertIn("expanded", self.targets(snapshot)["Country"].states)
        self.act(task, session, snapshot, option, "activate")
        self.wait_for(
            lambda: (
                self.value(
                    view.page(), "document.querySelector('[role=combobox]').textContent"
                )
                == "Norway"
            )
        )

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
