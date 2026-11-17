"""Private, version-specific worker bypass and task input frontend.

Verified against Qt 6.11.2, like cookie_snapshot.py. No debug listener or caller
scripts are exposed. Keep the frontend attached: detaching resets target policy.
"""

from concurrent.futures import Future

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWebEngineCore import QWebEnginePage

from browser import webengine
from services.task_input import TaskInput


class TaskNetworkError(RuntimeError):
    code = "native_frontend_unavailable"

    def __init__(self):
        super().__init__("Task network protection unavailable")


PREPARE_SCRIPT = """
window.__steedTaskNetwork = null;
(async () => {
    try {
        const SDK = await import(new URL('./core/sdk/sdk.js', location.href).href);
        const manager = SDK.TargetManager.TargetManager.instance();
        let target;
        for (let attempt = 0; attempt < 300; attempt++) {
            target = manager.primaryPageTarget()
                || manager.targets().find(t => t.type() === 'frame');
            if (target) break;
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        if (!target) throw new Error('Target unavailable');
        const response = await target.networkAgent().invoke_setBypassServiceWorker({bypass: true});
        if (response.getError()) throw new Error(response.getError());
        window.__steedTaskNetwork = true;
    } catch (_) { window.__steedTaskNetwork = false; }
})();
"""


# Loading the bundled DevTools frontend and attaching to the page can take many
# seconds on a busy machine. Waiting longer only delays a genuine failure.
READY_TIMEOUT_MS = 30000
POLL_MS = 100


class TaskNetwork(QObject):
    def __init__(self, page, parent=None, *, live=False):
        super().__init__(parent)
        self.page = page
        self.live = live
        self.ready = Future()
        self.closed = False
        self.attempts = 0
        self.started = False
        self.healthy = False
        # The bundled DevTools frontend has its own off-the-record storage;
        # task HTTP filters never need exceptions for its internal assets.
        self.frontend_profile = webengine.private_profile(self)
        self.frontend = QWebEnginePage(self.frontend_profile, self)
        self.input = TaskInput(self)
        self.frontend.loadFinished.connect(self._loaded)
        self.frontend.renderProcessTerminated.connect(lambda *_: self._fail())
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        page.setDevToolsPage(self.frontend)

    def _loaded(self, ok):
        if self.closed:
            return
        if not ok or self.started:
            self._fail()
            return
        self.started = True
        script = PREPARE_SCRIPT
        if self.live:
            script = script.replace("{bypass: true}", "{bypass: false}")
        self.frontend.runJavaScript(script)

    def _fail(self):
        self.healthy = False
        self.input.cancel("native_input_unavailable")
        if not self.live and not sip.isdeleted(self.page):
            self.page.network_ready = False
            webengine.trigger(self.page, "stop")
        self.timer.stop()
        if not self.ready.done():
            self.ready.set_exception(TaskNetworkError())

    def _poll(self):
        self.attempts += 1
        if self.attempts >= READY_TIMEOUT_MS // POLL_MS:
            self._fail()
        elif self.started:
            self.frontend.runJavaScript("window.__steedTaskNetwork", self._received)

    def _received(self, value):
        if self.closed or self.ready.done():
            return
        if value is False:
            self._fail()
        elif value is True:
            self.healthy = True
            self.timer.stop()
            self.ready.set_result(None)

    def validate(self):
        if self.closed or not self.healthy:
            raise TaskNetworkError()

    def close(self):
        self.closed = True
        self._fail()
        if not sip.isdeleted(self.page):
            self.page.setDevToolsPage(None)
        sip.delete(self.frontend)
        sip.delete(self.frontend_profile)
