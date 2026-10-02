"""Fixed native keyboard operations through the task's private Qt frontend."""

import json
from concurrent.futures import Future
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer

from services.page_inspector import PageInspectionError

KEYS = {
    "space": {"key": " ", "code": "Space", "windowsVirtualKeyCode": 32, "text": " "},
    "home": {"key": "Home", "code": "Home", "windowsVirtualKeyCode": 36},
    "down": {"key": "ArrowDown", "code": "ArrowDown", "windowsVirtualKeyCode": 40},
}

INPUT_SCRIPT = """
(() => {
    const request = /* REQUEST */;
    window[request.id] = null;
    (async () => {
        let target;
        try {
            const SDK = await import(new URL('./core/sdk/sdk.js', location.href).href);
            const manager = SDK.TargetManager.TargetManager.instance();
            target = manager.primaryPageTarget() || manager.targets().find(t => t.type() === 'frame');
            if (!target) throw new Error('Target unavailable');
            window.__steedInputTarget = target;
            if (window[request.id] === false) return;
            if (request.viewport) {
                const metrics = await target.emulationAgent().invoke_setDeviceMetricsOverride(
                    {width:1280,height:800,deviceScaleFactor:1,mobile:false});
                if (metrics.getError()) throw new Error('Viewport unavailable');
            }
            let response = await target.emulationAgent().invoke_setFocusEmulationEnabled({enabled: true});
            if (response.getError()) throw new Error('Focus unavailable');
            for (const key of request.keys) {
                if (window[request.id] === false) break;
                for (const type of ['keyDown', 'keyUp']) {
                    response = await target.inputAgent().invoke_dispatchKeyEvent({type, ...key});
                    if (response.getError()) throw new Error('Input unavailable');
                }
            }
            if (window[request.id] !== false) window[request.id] = true;
        } catch (_) { window[request.id] = false; }
        finally {
            if (target && window[request.id] === false)
                await target.emulationAgent().invoke_setFocusEmulationEnabled({enabled:false});
        }
    })();
})();
"""


class TaskInput(QObject):
    """No caller keys, scripts, coordinates, or DevTools commands are exposed."""

    def __init__(self, network):
        super().__init__(network)
        self.network = network
        self.pending = set()
        self.timeout_ms = 5000

    def keys(self, names, validate, *, viewport=False):
        if not 0 <= len(names) <= 32 or any(name not in KEYS for name in names):
            raise ValueError("Unsupported native input batch")
        validate()
        self.network.validate()
        result = Future()
        self.pending.add(result)
        identifier = "__steed_input_" + uuid4().hex
        poll = QTimer(self)
        poll.setInterval(20)
        deadline = QTimer(self)
        deadline.setSingleShot(True)

        def cleanup(_):
            self.pending.discard(result)
            poll.stop()
            deadline.stop()
            poll.deleteLater()
            deadline.deleteLater()
            if not self.network.closed:
                # The private async loop observes cancellation between keys.
                self.network.frontend.runJavaScript(
                    f"window[{json.dumps(identifier)}] = false"
                )

        def received(value):
            if result.done():
                return
            try:
                validate()
                self.network.validate()
                if value is False:
                    raise PageInspectionError("native_input_unavailable")
                if value is True:
                    result.set_result(None)
            except Exception as error:
                result.set_exception(error)

        def tick():
            if not result.done():
                self.network.frontend.runJavaScript(
                    f"window[{json.dumps(identifier)}]", received
                )

        def expire():
            if not result.done():
                result.set_exception(PageInspectionError("native_input_timeout"))

        result.add_done_callback(cleanup)
        poll.timeout.connect(tick)
        deadline.timeout.connect(expire)
        request = {
            "id": identifier,
            "keys": [KEYS[name] for name in names],
            "viewport": viewport,
        }
        script = INPUT_SCRIPT.replace("/* REQUEST */", json.dumps(request))
        try:
            self.network.frontend.runJavaScript(script)
            poll.start()
            deadline.start(self.timeout_ms)
        except Exception as error:
            result.set_exception(error)
        return result

    def cancel(self, reason="native_input_cancelled"):
        for result in tuple(self.pending):
            if not result.done():
                result.set_exception(PageInspectionError(reason))
        self.release_focus()

    def release_focus(self):
        if not self.network.closed:
            self.network.frontend.runJavaScript(
                "window.__steedInputTarget?.emulationAgent().invoke_setFocusEmulationEnabled({enabled:false})"
            )
