"""What sites store, and clearing it, without visiting them.

Qt WebEngine has no per-site storage API, so Steed asks Chromium through a
hidden Developer Tools page (as for full-page screenshots): Storage
getUsageAndQuota for how much a site keeps, and clearDataForOrigin for
everything it keeps (cookies, local and session storage, IndexedDB, cache
storage, service workers, file systems).
"""

import json
from uuid import uuid4

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

import webengine

TIMEOUT_MS = 30000
SCRIPT = """
(() => {
  const id = /* ID */, origins = /* ORIGINS */, clear = /* CLEAR */;
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
      const storage = target.storageAgent();
      const result = {usage: {}, errors: []};
      for (const origin of origins) {
        const used = await storage.invoke_getUsageAndQuota({origin});
        if (!used.getError()) result.usage[origin] = used.usage;
        if (clear) {
          const done = await storage.invoke_clearDataForOrigin({origin, storageTypes: 'all'});
          if (done.getError()) result.errors.push(origin + ': ' + done.getError());
        }
      }
      window[id] = JSON.stringify(result);
    } catch (error) { window[id] = JSON.stringify({error: String(error)}); }
  })();
  return true;
})()
"""


def _domain(host) -> bool:
    """A real domain name (not an IP address or a single word like localhost)."""
    import ipaddress

    try:
        ipaddress.ip_address(host.strip("[]"))
        return False
    except ValueError:
        return "." in host and all(part for part in host.split("."))


def safe_host(host) -> bool:
    """A host Chromium reads as itself. Measured: clearing the origin
    https://www.127.0.0.1 (which looks like an IP address but is not one)
    cleared another site's storage too, so such hosts are never sent."""
    import ipaddress

    host = (host or "").strip("[]")
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if not host or not QUrl(f"https://{host}/").isValid():
        return False
    last = host.rsplit(".", 1)[-1]
    return not last.isdigit() and all(part for part in host.split("."))


def safe_origin(origin) -> bool:
    url = QUrl(origin)
    return url.scheme() in ("http", "https") and safe_host(url.host())


def origins_for(hosts) -> list[str]:
    """http and https origins of hosts (with www. and without, for domains).

    Only well-formed origins: Chromium must never be handed one it could read
    as something wider."""
    found = []
    for host in hosts:
        host = (host or "").strip().lower().strip(".")
        if not safe_host(host):
            continue
        names = {host}
        if _domain(host):
            names.add(host[4:] if host.startswith("www.") else "www." + host)
        for name in sorted(names):
            for scheme in ("https", "http"):
                origin = f"{scheme}://[{name}]" if ":" in name else f"{scheme}://{name}"
                if safe_origin(origin) and origin not in found:
                    found.append(origin)
    return found


class SiteDataJob(QObject):
    """Measure (and with clear=True, clear) what origins store in a profile.

    Ends with finished({origin: bytes before}, [errors]) or failed(text)."""

    finished = pyqtSignal(dict, list)
    failed = pyqtSignal(str)

    def __init__(self, profile, origins, *, clear=False, parent=None):
        super().__init__(parent)
        self.profile = profile
        # Only origins Chromium reads exactly (see safe_host).
        self.origins = [o for o in dict.fromkeys(origins) if safe_origin(o)]
        self.clear = clear
        self.id = json.dumps("__steed_sites_" + uuid4().hex)
        self.page = self.frontend = None
        self.done = False
        self.poller = QTimer(self, interval=150)
        self.poller.timeout.connect(self._poll)
        self.limit = QTimer(self, singleShot=True, interval=TIMEOUT_MS)
        self.limit.timeout.connect(lambda: self._fail("Clearing took too long."))

    def start(self):
        if not self.origins:
            QTimer.singleShot(0, lambda: self._succeed({}, []))
            return
        # Any page of the profile will do: storage belongs to the profile.
        self.page = webengine.Page(self.profile, self)
        self.page.setUrl(QUrl("about:blank"))
        self.frontend = webengine.Page(self.profile, self)
        self.frontend.loadFinished.connect(self._ready)
        self.page.setDevToolsPage(self.frontend)
        self.limit.start()

    def _ready(self, ok):
        if self.done:
            return
        if not ok:
            self._fail("Developer Tools could not start.")
            return
        source = (
            SCRIPT.replace("/* ID */", self.id)
            .replace("/* ORIGINS */", json.dumps(self.origins))
            .replace("/* CLEAR */", "true" if self.clear else "false")
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
        except ValueError:
            self._fail("Chromium gave an unreadable answer.")
            return
        if "error" in result:
            self._fail(f"Could not reach site data ({result['error']}).")
            return
        usage = {
            k: int(v)
            for k, v in result.get("usage", {}).items()
            if isinstance(v, (int, float))
        }
        self._succeed(usage, [str(e) for e in result.get("errors", [])])

    def _succeed(self, usage, errors):
        if self.done:
            return
        self._finish()
        self.finished.emit(usage, errors)

    def _fail(self, message):
        if self.done:
            return
        self._finish()
        self.failed.emit(message)

    def _finish(self):
        self.done = True
        self.poller.stop()
        self.limit.stop()
        # Pages go before their profile: delete them now, not later.
        for name in ("frontend", "page"):
            page = getattr(self, name)
            setattr(self, name, None)
            if page is not None and not sip.isdeleted(page):
                if name == "frontend" and self.page is not None:
                    self.page.setDevToolsPage(None)
                page.deleteLater()

    def dispose(self):
        """Stop now (the profile is going): delete the pages at once."""
        self.done = True
        self.poller.stop()
        self.limit.stop()
        for name in ("frontend", "page"):
            page = getattr(self, name)
            setattr(self, name, None)
            if page is not None and not sip.isdeleted(page):
                sip.delete(page)


def human_size(count: int) -> str:
    size = float(count)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{count} bytes"
