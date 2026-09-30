"""Private in-process DevTools cookie enumeration, with no debug listener.

Qt 6.11's loadAllCookies implementation discards GetAllCookies' result; already
persisted cookies need not emit change events. Keep the workaround behind this
version-specific adapter. Never expose its privileged frontend as a browser tab.
"""

from PyQt6.QtCore import QDateTime, QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtWebEngineCore import QWebEnginePage


class CookieSnapshot(QObject):
    loaded = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, inspected, parent=None):
        super().__init__(parent)
        self.inspected = inspected
        self.frontend = QWebEnginePage(inspected.profile(), self)
        self.ready = False
        self.pending = False
        self.attempts = 0
        self.revision = 0
        self.request_revision = 0
        self.retries = 0
        self.closed = False
        self.skipped_partitioned = 0
        store = inspected.profile().cookieStore()
        store.cookieAdded.connect(self.store_changed)
        store.cookieRemoved.connect(self.store_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.frontend.loadFinished.connect(self.on_ready)
        inspected.setDevToolsPage(self.frontend)

    def store_changed(self, cookie):
        self.revision += 1
        QTimer.singleShot(0, self.refresh)

    def on_ready(self, ok):
        if self.closed:
            return
        self.ready = ok
        if ok:
            self.refresh()
        else:
            self.failed.emit("Cookie inspection frontend failed to initialize.")

    def refresh(self):
        if self.closed or not self.ready or self.pending:
            return
        self.pending = True
        self.attempts = 0
        self.request_revision = self.revision
        self.frontend.runJavaScript("""window.__browserCookieSnapshot = null;
            import(new URL('./core/sdk/sdk.js', location.href).href).then(async SDK => {
                const manager = SDK.TargetManager.TargetManager.instance();
                let target;
                for (let attempt = 0; attempt < 50; attempt++) {
                    target = manager.primaryPageTarget() || manager.targets().find(t => t.type() === 'frame');
                    if (target) break;
                    await new Promise(resolve => setTimeout(resolve, 100));
                }
                if (!target) throw new Error('Inspection target unavailable');
                const response = await target.networkAgent().invoke_getAllCookies({});
                if (response.getError()) throw new Error(response.getError());
                window.__browserCookieSnapshot = {cookies: response.cookies};
            }).catch(error => { window.__browserCookieSnapshot = {error:String(error)}; });
        """)
        self.timer.start()

    def poll(self):
        if self.closed:
            return
        self.attempts += 1
        if self.attempts > 80:
            self.pending = False
            self.timer.stop()
            self.failed.emit(
                "Cookie inspection timed out; cached live cookies are still available."
            )
            return
        self.frontend.runJavaScript(
            "window.__browserCookieSnapshot", self.accept_result
        )

    def accept_result(self, result):
        if self.closed or not self.pending or not isinstance(result, dict):
            return
        self.pending = False
        self.timer.stop()
        self.frontend.runJavaScript("window.__browserCookieSnapshot = null;")
        if self.revision != self.request_revision:
            self.retries += 1
            if self.retries <= 3:
                self.refresh()
            else:
                self.retries = 0
                self.failed.emit(
                    "Cookies changed during inspection; refresh again when the page is idle."
                )
            return
        self.retries = 0
        if result.get("error"):
            self.failed.emit("Cookie inspection unavailable: " + result["error"])
            return
        cookies = []
        self.skipped_partitioned = 0
        for record in result.get("cookies", []):
            if record.get("partitionKey"):
                self.skipped_partitioned += 1
                continue  # QNetworkCookie cannot represent a partition identity.
            cookie = QNetworkCookie(record["name"].encode(), record["value"].encode())
            cookie.setDomain(record["domain"])
            cookie.setPath(record["path"])
            cookie.setSecure(record.get("secure", False))
            cookie.setHttpOnly(record.get("httpOnly", False))
            if not record.get("session", True):
                cookie.setExpirationDate(
                    QDateTime.fromMSecsSinceEpoch(int(record["expires"] * 1000))
                )
            policy = {
                "Strict": QNetworkCookie.SameSite.Strict,
                "Lax": QNetworkCookie.SameSite.Lax,
                "None": QNetworkCookie.SameSite.None_,
            }.get(record.get("sameSite"))
            if policy is not None:
                cookie.setSameSitePolicy(policy)
            cookies.append(cookie)
        self.loaded.emit(cookies)

    def close(self):
        self.closed = True
        self.timer.stop()
        self.pending = False
        self.inspected.setDevToolsPage(None)
