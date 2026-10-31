"""Let a page run its unload handlers before Steed discards or deletes it.

Deleting or discarding a QWebEnginePage does not run beforeunload, pagehide or
unload. Sites save state there: Discord, for example, removes its login token
from local storage while open and writes it back on beforeunload, so a tab
closed, unloaded to save memory or left on a profile switch signed it out.
"""

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

# Handlers that hang never block closing for longer than this.
UNLOAD_TIMEOUT_MS = 1500
# Settings → "Let sites save before closing"; windows keep this in step.
ENABLED = True

UNLOAD_SCRIPT = """(() => {
    for (const type of ["beforeunload", "pagehide", "unload"]) {
        try { window.dispatchEvent(new Event(type)); } catch (_) {}
    }
    return true;
})()"""


def needs_unload(page):
    """Only ordinary web pages run site handlers worth waiting for."""
    return (
        ENABLED
        and page is not None
        and page.url().scheme() in ("http", "https")
        and page.lifecycleState() != QWebEnginePage.LifecycleState.Discarded
        and page.settings().testAttribute(
            page.settings().WebAttribute.JavascriptEnabled
        )
    )


def run_unload_handlers(pages, done, timeout_ms=UNLOAD_TIMEOUT_MS):
    """Dispatch the unload events in each page, then call done() once."""
    pending = {id(page): page for page in pages if needs_unload(page)}
    finished = False

    def finish():
        nonlocal finished
        if not finished:
            finished = True
            done()

    if not pending:
        finish()
        return

    def answered(key):
        pending.pop(key, None)
        if not pending:
            finish()

    for key, page in list(pending.items()):
        if page.lifecycleState() == QWebEnginePage.LifecycleState.Frozen:
            # A frozen page runs no script; wake it so the handlers can run.
            page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        page.runJavaScript(
            UNLOAD_SCRIPT,
            QWebEngineScript.ScriptWorldId.MainWorld.value,
            lambda _, key=key: answered(key),
        )
    QTimer.singleShot(timeout_ms, finish)


def run_unload_handlers_now(pages, timeout_ms=UNLOAD_TIMEOUT_MS):
    """Blocking form for closing a window: returns once handlers have run.

    Waits in a local event loop that ignores user input, so nothing else in the
    window changes underneath the closing code.
    """
    loop = QEventLoop()
    finished = []

    def done():
        finished.append(True)
        loop.quit()

    run_unload_handlers(pages, done, timeout_ms)
    if not finished:
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
