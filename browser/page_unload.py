"""Let a page run its unload handlers before Steed discards or deletes it.

Deleting or discarding a QWebEnginePage does not run beforeunload, pagehide or
unload. Sites save state there: Discord, for example, removes its login token
from local storage while open and writes it back on beforeunload, so a tab
closed, unloaded to save memory or left on a profile switch signed it out.
"""

from PyQt6.QtCore import QEventLoop, QTimer

from browser import webengine

# Handlers that hang never block closing for longer than this.
UNLOAD_TIMEOUT_MS = 1500
# Unloading a background tab waits longer: nobody is waiting, and a frozen page
# can take seconds to wake. A page that does not answer is not unloaded.
DISCARD_TIMEOUT_MS = 5000
# What a page writes to storage reaches the browser separately from the script's
# answer; this short pause lets it land before the page goes.
SETTLE_MS = 300
# Settings → "Let sites save before closing"; windows keep this in step.
ENABLED = True

# What Chrome sends before discarding (freeze) or closing a page.
UNLOAD_SCRIPT = """(() => {
    try { document.dispatchEvent(new Event("freeze")); } catch (_) {}
    try { window.dispatchEvent(new Event("beforeunload", {cancelable: true})); } catch (_) {}
    try { window.dispatchEvent(new PageTransitionEvent("pagehide", {persisted: false})); } catch (_) {}
    try { window.dispatchEvent(new Event("unload")); } catch (_) {}
    return true;
})()"""


def needs_unload(page):
    """Only ordinary web pages run site handlers worth waiting for."""
    return (
        ENABLED
        and page is not None
        and page.url().scheme() in ("http", "https")
        and webengine.tab_state(page) != webengine.UNLOADED
        and webengine.option(page.settings(), "javascript")
    )


def run_unload_handlers(pages, done, timeout_ms=UNLOAD_TIMEOUT_MS):
    """Dispatch the unload events in each page, then call done(answered) once.

    answered is False when a page did not report back in time.
    """
    pending = {id(page): page for page in pages if needs_unload(page)}
    finished = False

    def finish(answered):
        nonlocal finished
        if not finished:
            finished = True
            done(answered)

    if not pending:
        finish(True)
        return

    def answered(key):
        pending.pop(key, None)
        if not pending and not finished:
            QTimer.singleShot(SETTLE_MS, lambda: finish(True))

    for key, page in list(pending.items()):
        if webengine.tab_state(page) == webengine.FROZEN:
            # A frozen page runs no script; wake it so the handlers can run.
            webengine.wake(page)
        webengine.run_script(
            page,
            UNLOAD_SCRIPT,
            lambda _, key=key: answered(key),
            world=webengine.PAGE_WORLD,
        )
    QTimer.singleShot(timeout_ms, lambda: finish(False))


def run_unload_handlers_now(pages, timeout_ms=UNLOAD_TIMEOUT_MS):
    """Blocking form for closing a window: returns once handlers have run.

    Waits in a local event loop that ignores user input, so nothing else in the
    window changes underneath the closing code.
    """
    loop = QEventLoop()
    finished = []

    def done(_answered):
        finished.append(True)
        loop.quit()

    run_unload_handlers(pages, done, timeout_ms)
    if not finished:
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
