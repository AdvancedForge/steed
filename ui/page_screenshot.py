"""Screenshots of a tab: the visible area, or the whole page.

The visible area is the tab as drawn. The whole page is captured by Chromium
itself through a hidden DevTools page (the same route as Steed's cookie
inspection and agent viewport images), so the page is not resized or scrolled.
"""

import base64
import json
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QImage

import webengine

MAX_WIDTH = 4096
MAX_HEIGHT = 16384  # Chromium's largest surface; longer pages are cut here.
TIMEOUT_MS = 30000
SCRIPT = """
(() => {
  const id = /* ID */;
  window[id] = null;
  (async () => {
    try {
      const SDK = await import(new URL('./core/sdk/sdk.js', location.href).href);
      const manager = SDK.TargetManager.TargetManager.instance();
      let target = null;
      for (let i = 0; i < 50 && !target; i++) {
        target = manager.primaryPageTarget() || manager.targets().find(t => t.type() === 'frame');
        if (!target) await new Promise(r => setTimeout(r, 100));
      }
      if (!target) throw new Error('target');
      const agent = target.pageAgent();
      const metrics = await agent.invoke_getLayoutMetrics({});
      const size = metrics.cssContentSize;
      if (metrics.getError() || !size) throw new Error('metrics');
      const width = Math.max(1, Math.min(/* W */, Math.ceil(size.width)));
      const height = Math.max(1, Math.min(/* H */, Math.ceil(size.height)));
      const shot = await agent.invoke_captureScreenshot({format: 'png',
        captureBeyondViewport: true, fromSurface: true,
        clip: {x: 0, y: 0, width, height, scale: 1}});
      if (shot.getError() || !shot.data) throw new Error('capture');
      window[id] = JSON.stringify({data: shot.data, cut: Math.ceil(size.height) > height});
    } catch (error) { window[id] = JSON.stringify({error: String(error)}); }
  })();
  return true;
})()
"""


def visible_area(tab):
    """The tab as drawn now, as a QImage."""
    return tab.grab().toImage()


class FullPageCapture(QObject):
    """Captures one whole page, then reports finished(image) or failed(text)."""

    finished = pyqtSignal(QImage, bool)  # image, whether a long page was cut
    failed = pyqtSignal(str)

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.frontend = None
        self.id = json.dumps("__steed_shot_" + uuid4().hex)
        self.done = False
        self.poller = QTimer(self, interval=200)
        self.poller.timeout.connect(self._poll)
        self.limit = QTimer(self, singleShot=True, interval=TIMEOUT_MS)
        self.limit.timeout.connect(lambda: self._fail("The page took too long."))

    def start(self):
        if self.page.devToolsPage() is not None:
            self._fail("Close Developer Tools for this tab, then try again.")
            return
        self.frontend = webengine.Page(self.page.profile(), self)
        self.frontend.loadFinished.connect(self._ready)
        self.page.setDevToolsPage(self.frontend)
        self.limit.start()

    def _ready(self, ok):
        if self.done:
            return
        if not ok:
            self._fail("Developer Tools could not start for this page.")
            return
        source = (
            SCRIPT.replace("/* ID */", self.id)
            .replace("/* W */", str(MAX_WIDTH))
            .replace("/* H */", str(MAX_HEIGHT))
        )
        webengine.run_script(
            self.frontend,
            source,
            lambda _: self.poller.start(),
            world=webengine.PAGE_WORLD,
        )

    def _poll(self):
        if self.done or self.frontend is None:
            return
        webengine.run_script(
            self.frontend,
            f"window[{self.id}]",
            self._answer,
            world=webengine.PAGE_WORLD,
        )

    def _answer(self, raw):
        if self.done or not isinstance(raw, str):
            return
        try:
            result = json.loads(raw)
            if "error" in result:
                raise ValueError(result["error"])
            image = QImage.fromData(base64.b64decode(result["data"]), "PNG")
        except (ValueError, KeyError, TypeError):
            self._fail("The page could not be captured.")
            return
        if image.isNull():
            self._fail("The page could not be captured.")
            return
        self._finish()
        self.finished.emit(image, bool(result.get("cut")))

    def _fail(self, message):
        if self.done:
            return
        self._finish()
        self.failed.emit(message)

    def _finish(self):
        self.done = True
        self.poller.stop()
        self.limit.stop()
        if self.frontend is not None:
            if (
                not sip.isdeleted(self.page)
                and self.page.devToolsPage() is self.frontend
            ):
                self.page.setDevToolsPage(None)
            frontend, self.frontend = self.frontend, None
            frontend.deleteLater()
