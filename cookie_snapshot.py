"""Private in-process DevTools cookie enumeration, with no debug listener.

Qt 6.11's loadAllCookies implementation discards GetAllCookies' result; already
persisted cookies need not emit change events. Keep the workaround behind this
version-specific adapter. Never expose its privileged frontend as a browser tab.

Bumping Qt means re-verifying every private name used below.
"""

from PyQt6.QtCore import QDateTime, QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtWebEngineCore import QWebEnginePage

# The last Qt release these private Chromium internals were confirmed against.
VERIFIED_AGAINST_QT = "6.11.2"

# Consecutive inspection rounds that may be discarded because the store changed
# mid-flight, and how long to wait before trying again once the immediate retries
# are exhausted. Bounding both keeps a busy store from spinning forever while a
# short-lived write burst still converges on its own.
MAX_COOLDOWN_RETRIES = 3
COOLDOWN_MS = 400

# The injected script waits up to TARGET_WAIT_ATTEMPTS * POLL_MS for a
# inspectable target before giving up, so the polling budget has to exceed that
# on its own or a healthy-but-slow page reports a spurious timeout.
POLL_MS = 100
TARGET_WAIT_ATTEMPTS = 50
MAX_POLL_ATTEMPTS = 300

# A page whose targets only become inspectable after startup (or after the
# engine is busy) should still be enumerated, so a timeout is retried a bounded
# number of times rather than leaving the viewer permanently empty.
MAX_TIMEOUT_RETRIES = 2

# Window used to merge a burst of cookie writes into a single inspection.
REFRESH_DEBOUNCE_MS = 120

# Runs on the hidden DevTools frontend page. The SDK import and target manager
# are memoized on the page so a second inspection costs one protocol call rather
# than a fresh module load. The target is re-resolved every time because the
# inspected page is not stable across a session.
SNAPSHOT_SCRIPT = """window.__browserCookieRequest = REQUEST_ID;
window.__browserCookieSnapshot = null;
(() => {
    const requestId = REQUEST_ID;
    const publish = result => {
        if (window.__browserCookieRequest === requestId) {
            window.__browserCookieSnapshot = result;
        }
    };
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const sdk = window.__browserCookieSdk
        || (window.__browserCookieSdk =
              import(new URL('./core/sdk/sdk.js', location.href).href));
    return sdk.then(SDK => {
        const manager = SDK.TargetManager.TargetManager.instance();
        const findTarget = attempt => {
            const target = manager.primaryPageTarget()
                || manager.targets().find(t => t.type() === 'frame');
            if (target) {
                return target.networkAgent().invoke_getAllCookies({}).then(response => {
                    if (response.getError()) {
                        throw new Error(response.getError());
                    }
                    publish({cookies: response.cookies});
                });
            }
            if (attempt >= 50) {
                publish({
                    error: 'Inspection target unavailable'
                });
                return;
            }
            return wait(100).then(() => findTarget(attempt + 1));
        };
        return findTarget(0);
    }).catch(error => {
        publish({error: String(error)});
    });
})();"""


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
        self.request_id = 0
        self.revision = 0
        self.request_revision = 0
        self.retries = 0
        self.cooldowns = 0
        self.timeouts = 0
        self.timed_out = False
        self.retryable_error = False
        self.dirty = False
        self.closed = False
        self.skipped_partitioned = 0
        store = inspected.profile().cookieStore()
        store.cookieAdded.connect(self.store_changed)
        store.cookieRemoved.connect(self.store_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll)
        self.frontend.loadFinished.connect(self.on_ready)
        inspected.setDevToolsPage(self.frontend)

    def store_changed(self, cookie):
        self.revision += 1
        # A write is new information: discard the backoff and re-inspect at once.
        self.cooldowns = 0
        self.timeouts = 0
        # Coalesce: one page load writes several cookies, and each snapshot is a
        # full DevTools round trip. Debouncing collapses a burst into one.
        QTimer.singleShot(REFRESH_DEBOUNCE_MS, self.refresh)

    def on_ready(self, ok):
        if self.closed:
            return
        self.ready = ok
        if ok:
            self.refresh()
        else:
            self.failed.emit("Cookie inspection frontend failed to initialize.")

    def refresh(self):
        if self.closed or not self.ready:
            return
        if self.pending:
            # Dropping this request would lose the update entirely: Qt signals
            # for an overwritten cookie arrive while the previous inspection is
            # still in flight, and nothing would trigger a follow-up.
            self.dirty = True
            return
        self.pending = True
        self.dirty = False
        self.attempts = 0
        self.timed_out = False
        self.request_revision = self.revision
        self.request_id += 1
        # The SDK module and its target manager are cached on the frontend page
        # across refreshes: re-importing per snapshot dominated the wall time
        # and delayed every enumeration behind a fresh module evaluation.
        self.frontend.runJavaScript(
            SNAPSHOT_SCRIPT.replace("REQUEST_ID", str(self.request_id))
        )
        self.timer.start()

    def _resume_after_cooldown(self):
        """Retry after a write burst or a timeout; a change supersedes this run."""
        if self.closed:
            return
        if self.revision != self.request_revision:
            self.refresh()
            return
        if self.timed_out and self.timeouts <= MAX_TIMEOUT_RETRIES:
            # A timeout with no concurrent writes means inspection simply had not
            # settled yet, not that the store is busy. Try again, bounded.
            self.refresh()
            return
        if self.retryable_error:
            # The previous round failed because no target was inspectable yet.
            self.retryable_error = False
            self.refresh()

    def _give_up(self, message):
        self.pending = False
        self.timer.stop()
        self.timed_out = True
        self.failed.emit(message)

    def poll(self):
        if self.closed:
            return
        self.attempts += 1
        if self.attempts > MAX_POLL_ATTEMPTS:
            self.timeouts += 1
            if self.timeouts <= MAX_TIMEOUT_RETRIES:
                self.pending = False
                self.timer.stop()
                self.timed_out = True
                QTimer.singleShot(COOLDOWN_MS, self._resume_after_cooldown)
            else:
                self._give_up(
                    "Cookie inspection timed out; cached live cookies are still available."
                )
            return
        request_id = self.request_id
        self.frontend.runJavaScript(
            "window.__browserCookieSnapshot",
            lambda result: self.accept_result(result, request_id),
        )

    def accept_result(self, result, request_id=None):
        if (
            self.closed
            or not self.pending
            or (request_id is not None and request_id != self.request_id)
            or not isinstance(result, dict)
        ):
            return
        self.pending = False
        self.timer.stop()
        self.frontend.runJavaScript("window.__browserCookieSnapshot = null;")
        if self.revision != self.request_revision:
            # The store changed while the round trip was in flight, so this
            # result is already out of date. Re-run immediately a few times,
            # then back off: a burst of writes must not leave the viewer
            # permanently stale just because inspection kept losing the race.
            self.retries += 1
            if self.retries <= 3:
                self.refresh()
                return
            self.retries = 0
            if self.cooldowns >= MAX_COOLDOWN_RETRIES:
                self.failed.emit(
                    "Cookies kept changing during inspection; showing the last "
                    "confirmed state. Reopen the viewer when the page is idle."
                )
                return
            self.cooldowns += 1
            QTimer.singleShot(COOLDOWN_MS, self._resume_after_cooldown)
            return
        self.retries = 0
        if self.dirty:
            # A change arrived while this result was in flight, so it cannot be
            # the answer to it. Inspect again before reporting anything.
            self.dirty = False
            self.refresh()
            return
        if result.get("error"):
            # "Inspection target unavailable" is transient while the engine
            # starts a page, so retry it instead of failing the viewer for the
            # rest of the session. Give up once the bound is reached.
            message = result["error"]
            if (
                "target unavailable" in message.lower()
                and self.cooldowns < MAX_COOLDOWN_RETRIES
            ):
                self.cooldowns += 1
                self.retryable_error = True
                QTimer.singleShot(COOLDOWN_MS, self._resume_after_cooldown)
                return
            self.failed.emit("Cookie inspection unavailable: " + message)
            return
        # Failed rounds must retain their budget; only a confirmed result
        # starts a fresh recovery cycle.
        self.cooldowns = 0
        self.timeouts = 0
        self.retryable_error = False
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
