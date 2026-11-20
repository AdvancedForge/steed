"""Saved steps: a routine's clicks and typing, replayed without a model.

A routine can carry a short list of steps that Steed runs itself in your
profile before reading: open a page, click something by its label, type into
a field, choose an option, wait for some text, read the page. The steps come
from what an agent actually did in a chat (Steed notes its page actions, in
memory only) or from the agent's own proposal, and you review them before
they are saved. When a step no longer works (the site changed), the routine
hands its usual message to your agent, saying where the steps stopped.

Password and payment card fields are never typed into, as everywhere else.
"""

import json

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

from browser import webengine

MAX_STEPS = 20
TEXT_LIMIT = 500
STEP_TIMEOUT_MS = 10000
LOAD_TIMEOUT_MS = 30000
SETTLE_MS = 1200
POLL_MS = 400
ACTIONS = ("open", "click", "type", "choose", "wait", "read")


def check_steps(steps):
    """Steps as given, cleaned; ValueError says what is wrong."""
    if steps in (None, []):
        return []
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise ValueError(f"Steps are a list of at most {MAX_STEPS}.")
    clean = []
    for number, step in enumerate(steps, 1):
        if not isinstance(step, dict) or len(step) == 0:
            raise ValueError(f"Step {number} is not a step.")
        action = next((a for a in ACTIONS if a in step), None)
        if action is None:
            raise ValueError(f"Step {number}: use one of {', '.join(ACTIONS)}.")
        allowed = {
            "open": {"open"},
            "click": {"click"},
            "type": {"type", "text"},
            "choose": {"choose", "option"},
            "wait": {"wait"},
            "read": {"read"},
        }[action]
        if set(step) != allowed:
            raise ValueError(
                f"Step {number}: {action} takes {', '.join(sorted(allowed))}."
            )
        if action == "open":
            url = QUrl(str(step["open"]).strip())
            if url.scheme() not in ("http", "https") or not url.host():
                raise ValueError(f"Step {number}: open a web address.")
            clean.append({"open": url.toString()})
        elif action == "read":
            clean.append({"read": True})
        else:
            values = {}
            for key in allowed:
                value = step[key]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"Step {number}: {key} needs some text.")
                if len(value) > TEXT_LIMIT or "\x00" in value:
                    raise ValueError(f"Step {number}: {key} is too long.")
                values[key] = value.strip() if key != "text" else value
            clean.append(values)
    if not clean or "open" not in clean[0]:
        raise ValueError("Saved steps start by opening a page.")
    return clean


def describe_step(step):
    """One step in words, for review and for the agent."""
    if "open" in step:
        return f"Open {step['open']}"
    if "click" in step:
        return f"Click “{step['click']}”"
    if "type" in step:
        return f"Type “{step['text']}” into “{step['type']}”"
    if "choose" in step:
        return f"Choose “{step['option']}” in “{step['choose']}”"
    if "wait" in step:
        return f"Wait for “{step['wait']}”"
    return "Read the page"


# Runs one step in Steed's own script world: finds the control by what it
# says (its label, aria-label, placeholder or text), acts, and reports.
STEP_SCRIPT = r"""
(() => {
    const STEP = /* STEP */;
    const words = s => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
    const CARD = /(card.?(number|num|no)|cc.?(num|number)|\bcvv|\bcvc|\bcsc\b|security.?code)/i;
    const sensitive = el => el.tagName === "INPUT" && (el.type === "password" ||
        (el.getAttribute("type") || "").toLowerCase() === "password" ||
        (el.getAttribute("autocomplete") || "").toLowerCase().includes("cc-") ||
        CARD.test([el.name, el.id, el.placeholder, el.getAttribute("aria-label")].join(" ")));
    const shown = el => el.getClientRects().length && getComputedStyle(el).visibility !== "hidden";
    const nameOf = el => words(el.getAttribute("aria-label") || (el.labels && el.labels[0] && el.labels[0].innerText) ||
        el.getAttribute("placeholder") || el.getAttribute("title") || el.innerText || el.value || "");
    const find = (selector, label) => {
        const want = words(label);
        const all = [...document.querySelectorAll(selector)].filter(shown);
        return all.find(el => nameOf(el) === want) || all.find(el => nameOf(el).includes(want));
    };
    if (STEP.wait !== undefined)
        return {ok: words(document.body ? document.body.innerText : "").includes(words(STEP.wait))};
    if (STEP.read !== undefined)
        return {ok: true, title: document.title, text: document.body ? document.body.innerText : ""};
    if (STEP.click !== undefined) {
        const el = find("a, button, input[type=submit], input[type=button], [role=button], [role=link], [role=tab], [role=menuitem], summary", STEP.click);
        if (!el) return {ok: false, error: "not_found"};
        if (el.disabled) return {ok: false, error: "disabled"};
        const form = el.form || el.closest("form");
        if (form && [...form.querySelectorAll("input")].some(f => f.value && sensitive(f)))
            return {ok: false, error: "sensitive_form"};
        el.click();
        return {ok: true};
    }
    if (STEP.type !== undefined) {
        const el = find("input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea, [contenteditable=true]", STEP.type);
        if (!el) return {ok: false, error: "not_found"};
        if (sensitive(el)) return {ok: false, error: "sensitive_field"};
        el.focus();
        if (el.isContentEditable) el.textContent = STEP.text;
        else {
            const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, "value").set.call(el, STEP.text);
        }
        el.dispatchEvent(new Event("input", {bubbles: true}));
        el.dispatchEvent(new Event("change", {bubbles: true}));
        return {ok: true};
    }
    if (STEP.choose !== undefined) {
        const el = find("select", STEP.choose);
        if (!el) return {ok: false, error: "not_found"};
        const option = [...el.options].find(o => words(o.text) === words(STEP.option)) ||
            [...el.options].find(o => words(o.text).includes(words(STEP.option)));
        if (!option) return {ok: false, error: "option_not_found"};
        el.value = option.value;
        el.dispatchEvent(new Event("change", {bubbles: true}));
        return {ok: true};
    }
    return {ok: false, error: "unknown_step"};
})()
"""

REASONS = {
    "not_found": "could not find it on the page",
    "disabled": "it was greyed out",
    "sensitive_form": "it would send a password or card, which only you do",
    "sensitive_field": "it is a password or card field, which only you fill",
    "option_not_found": "that option was not there",
    "not_seen": "that text never appeared",
    "not_loaded": "the page did not load",
    "timeout": "the page took too long",
}


class StepRunner(QObject):
    """Runs saved steps on one background page; finished(pages, failure)
    where pages is [(title, url, text)] read and failure None or
    (step number, step, reason)."""

    finished = pyqtSignal(list, object)

    def __init__(self, profile, steps, parent=None):
        super().__init__(parent)
        self.steps = list(steps)
        self.index = 0
        self.pages = []
        self.done = False
        self.page = webengine.background_page(profile, self)
        self.page.loadFinished.connect(self._loaded)
        self.page.loadStarted.connect(self._started)
        self.loading = False
        self.waiting_load = False
        self.waited = 0
        self.timer = QTimer(self, singleShot=True)

    def start(self):
        self._next()

    def stop(self):
        self.done = True
        self.timer.stop()
        if not sip.isdeleted(self.page):
            self.page.deleteLater()

    def _finish(self, failure=None):
        if self.done:
            return
        self.stop()
        self.finished.emit(self.pages, failure)

    def _fail(self, reason):
        self._finish((self.index + 1, self.steps[self.index], reason))

    def _next(self):
        if self.done:
            return
        if self.index >= len(self.steps):
            self._finish()
            return
        step = self.steps[self.index]
        if "open" in step:
            self.waiting_load = True
            self._timeout(LOAD_TIMEOUT_MS)
            self.page.load(QUrl(step["open"]))
            return
        if self.loading:  # A click is still loading its page.
            self.waited += POLL_MS
            if self.waited >= LOAD_TIMEOUT_MS:
                self._fail("timeout")
            else:
                QTimer.singleShot(POLL_MS, self._next)
            return
        self.waited = 0
        self._run(step)

    def _timeout(self, ms):
        try:
            self.timer.timeout.disconnect()
        except TypeError:
            pass
        self.timer.timeout.connect(lambda: self._fail("timeout"))
        self.timer.start(ms)

    def _started(self):
        self.loading = True

    def _loaded(self, ok):
        self.loading = False
        if self.done or not self.waiting_load:
            return
        self.waiting_load = False
        self.timer.stop()
        if not ok:
            self._fail("not_loaded")
            return
        self.index += 1
        self.waited = 0
        QTimer.singleShot(SETTLE_MS, self._next)

    def _run(self, step):
        if self.done or sip.isdeleted(self.page):
            return
        webengine.run_script(
            self.page,
            STEP_SCRIPT.replace("/* STEP */", json.dumps(step)),
            lambda result: self._ran(step, result),
            world=webengine.STEED_WORLD,
        )

    def _ran(self, step, result):
        if self.done:
            return
        result = result if isinstance(result, dict) else {}
        if "wait" in step and not result.get("ok"):
            self.waited += POLL_MS
            if self.waited >= STEP_TIMEOUT_MS:
                self._fail("not_seen")
            else:
                QTimer.singleShot(POLL_MS, lambda: self._run(step))
            return
        if not result.get("ok"):
            self._fail(str(result.get("error") or "not_found"))
            return
        if "read" in step:
            self.pages.append(
                (
                    str(result.get("title") or ""),
                    self.page.url().toString(),
                    str(result.get("text") or ""),
                )
            )
        self.index += 1
        self.waited = 0
        # Clicks may load a new page or redraw this one: give it a moment.
        QTimer.singleShot(SETTLE_MS if "click" in step else 100, self._next)
