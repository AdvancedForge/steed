"""Native filtering with asynchronous last-known-good list updates."""

import json
import threading

import adblock
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

from browser import extension_rules
from browser.utils import load_json, save_json

SOURCES = (
    "https://easylist.to/easylist/easylist.txt",
    "https://easylist.to/easylist/easyprivacy.txt",
)
BOOTSTRAP = [
    "||doubleclick.net^",
    "||googlesyndication.com^",
    "||googleadservices.com^",
    "||adservice.google.com^",
    "||amazon-adsystem.com^",
    "||adsrvr.org^",
    "||scorecardresearch.com^",
    "||taboola.com^",
    "||outbrain.com^",
    "||criteo.com^",
    "||criteo.net^",
    "||pubmatic.com^",
    "||rubiconproject.com^",
]


class FilterEngine:
    """Compile off the request path; serialize access to the Rust binding."""

    def __init__(self, rules):
        filters = adblock.FilterSet()
        filters.add_filters(rules)
        self.engine = adblock.Engine(filters, optimize=True)
        self.lock = threading.RLock()

    def blocked(self, url, source, resource):
        with self.lock:
            return self.engine.check_network_urls(url, source, resource).matched

    def selectors(self, url, classes=(), ids=()):
        with self.lock:
            resources = self.engine.url_cosmetic_resources(url)
            selectors = list(resources.hide_selectors)
            if not resources.generichide:
                selectors.extend(
                    self.engine.hidden_class_id_selectors(
                        list(classes), list(ids), resources.exceptions
                    )
                )
            return selectors


class FilterInterceptor(QWebEngineUrlRequestInterceptor):
    blocked = pyqtSignal(str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.profile = parent

    def interceptRequest(self, info):
        # Extensions' network rules (extension_rules) come first, as in Chrome.
        if self.profile is not None and extension_rules.apply(self.profile, info):
            return
        request_url = info.requestUrl()
        scheme = request_url.scheme()
        if not self.service.applies_now() or scheme not in ("http", "https"):
            return
        # Exception match on the host string directly; no QUrl per request.
        if self.service.host_excluded(request_url.host()):
            return
        source = info.firstPartyUrl().toString()
        resource_type = info.resourceType().name.removeprefix("ResourceType").lower()
        resource = {
            "mainframe": "document",
            "subframe": "subdocument",
            "xhr": "xmlhttprequest",
        }.get(resource_type, resource_type)
        if self.service.engine.blocked(request_url.toString(), source, resource):
            info.block(True)
            self.blocked.emit(request_url.host())


class AdBlocker(QObject):
    changed = pyqtSignal()
    compiled = pyqtSignal(object, object)
    compilation_failed = pyqtSignal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.cache = config.config_dir / "filter_lists.json"
        cached = load_json(self.cache, [])
        valid = (
            isinstance(cached, list)
            and cached
            and all(isinstance(r, str) for r in cached)
        )
        self.status = (
            "Cached filter lists loaded"
            if valid
            else "Bootstrap rules only; update filter lists for full coverage"
        )
        # A full EasyList compile takes seconds, so it never runs on the GUI
        # thread. Start with the cheap bootstrap rules and swap in the cached
        # list once a worker finishes.
        self.engine = FilterEngine(BOOTSTRAP)
        self.compiling = False
        self._pending_rules = cached if valid else None
        self.count = 0
        self.network = QNetworkAccessManager(self)
        self.replies: list[QNetworkReply] = []
        self.payloads = []
        self.updating = False
        # Request interception must not touch config, JSON, or QUrl per request.
        self._apply_enabled = True
        self._apply_exceptions: tuple[str, ...] = ()
        self._apply_revision = -1
        self._refresh_exceptions()
        self.compiled.connect(self.install)
        self.compilation_failed.connect(self.fail)
        # Initialize state and connect both outcomes before starting a worker:
        # even a tiny cached list can finish before __init__ returns.
        if valid:
            self._compile_async(cached, self.status)

    def _compile_async(self, rules, success_status):
        """Compile rules off the GUI thread and install them via the event loop."""
        self.compiling = True
        self.status = "Compiling filter lists..."
        self.changed.emit()

        def compile_rules():
            try:
                engine = FilterEngine(rules)
            except Exception as error:
                try:
                    self.compilation_failed.emit(str(error))
                except RuntimeError:
                    pass  # Application was closed while compilation ran.
                return
            try:
                self.compiled.emit(engine, rules)
            except RuntimeError:
                pass  # Application was closed while compilation ran.

        threading.Thread(target=compile_rules, daemon=True).start()

    def attach(self, profile):
        interceptor = FilterInterceptor(self, profile)
        interceptor.blocked.connect(self.note_blocked)
        profile.adblock_interceptor = interceptor
        profile.setUrlRequestInterceptor(interceptor)

    def _refresh_exceptions(self, force=False):
        """Recompute the per-request cache from configuration.

        The request path only compares an integer revision, so any direct config
        write still takes effect without re-parsing the exception list.
        """
        revision = getattr(self.config, "revision", 0)
        if not force and revision == self._apply_revision:
            return
        self._apply_revision = revision
        raw = self.config.get("adblock_exceptions", [])
        if isinstance(raw, list):
            sites = {
                site.strip().lower().lstrip(".")
                for site in raw
                if isinstance(site, str)
            }
        else:
            sites = set()
        self._apply_enabled = bool(self.config.get("adblock_enabled", True))
        self._apply_exceptions = tuple(sorted(sites))
        self._apply_revision = revision

    def applies_now(self):
        """Cheap request-path gate; the caller still checks the host.

        Also refreshes the cached exceptions, so interceptRequest can rely on
        _apply_enabled and host_excluded without re-reading configuration.
        """
        self._refresh_exceptions()
        return self._apply_enabled

    def applies(self, url):
        self._refresh_exceptions()
        if not self._apply_enabled:
            return False
        host = QUrl(url).host().lower()
        return bool(host) and not self.host_excluded(host)

    def host_excluded(self, host):
        """Exception check for an already-known host; avoids building a QUrl."""
        self._refresh_exceptions()
        if not self._apply_enabled or not host:
            return False
        return any(
            host == site or host.endswith("." + site) for site in self._apply_exceptions
        )

    def note_blocked(self, host):
        self.count += 1
        self.changed.emit()

    def update(self):
        if self.updating or self.compiling:
            return
        self.updating = True
        self.payloads = []
        self.status = "Downloading filter lists..."
        self.changed.emit()
        self.fetch(0)

    def refresh_settings(self):
        """Re-read toggles after the settings or ad-blocking dialog changes them."""
        self._refresh_exceptions(force=True)
        self.changed.emit()

    def fetch(self, index):
        if index == len(SOURCES):
            self.status = "Compiling filter lists..."
            self.changed.emit()
            rules = [
                line.strip()
                for payload in self.payloads
                for line in payload.splitlines()
                if line.strip() and not line.startswith(("!", "["))
            ]
            self._compile_async(rules, None)
            return
        request = QNetworkRequest(QUrl(SOURCES[index]))
        request.setTransferTimeout(20_000)
        reply = self.network.get(request)
        self.replies.append(reply)
        reply.downloadProgress.connect(
            lambda received, total: reply.abort() if received > 10_000_000 else None
        )

        def finished():
            if reply in self.replies:
                self.replies.remove(reply)
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self.fail("Filter download failed; previous rules retained.")
            else:
                payload = bytes(reply.readAll()).decode("utf-8", errors="replace")
                if (
                    len(payload) > 10_000_000
                    or payload.lstrip().startswith("<")
                    or len(payload.splitlines()) < 10
                ):
                    self.fail("Invalid filter list; previous rules retained.")
                else:
                    self.payloads.append(payload)
                    self.fetch(index + 1)
            reply.deleteLater()

        reply.finished.connect(finished)

    def install(self, engine, rules):
        self.compiling = False
        self._pending_rules = None
        if self.updating:
            # Compiled rules are always written back to the same cache file.
            try:
                save_json(self.cache, rules)
            except OSError:
                self.fail("Could not save updated rules; previous rules retained.")
                return
        self.engine = engine
        self.updating = False
        self.status = f"{len(rules):,} rules loaded; network and CSS hiding supported (scriptlets/redirects not enabled)"
        self.changed.emit()

    def fail(self, message):
        self.compiling = False
        self._pending_rules = None
        self.updating = False
        self.status = message
        self.changed.emit()

    def apply_cosmetic(self, tab):
        from PyQt6 import sip

        url = tab.url().toString()
        if not self.applies(url):
            return

        def apply(data):
            if (
                sip.isdeleted(tab)
                or tab.url().toString() != url
                or not isinstance(data, dict)
                or not self.applies(url)
            ):
                return
            styles = []
            for document in data.get("documents", []):
                document_url = document["url"]
                if not self.applies(document_url):
                    continue
                selectors = self.engine.selectors(
                    document_url, document["classes"], document["ids"]
                )
                styles.append(
                    {
                        "path": document["path"],
                        "documentUrl": document["documentUrl"],
                        "css": "\n".join(
                            selector + " {display:none!important;}"
                            for selector in selectors
                        ),
                    }
                )
            script = """(() => {
                for (const entry of STYLES) {
                    try {
                        let doc = document;
                        for (const index of entry.path) {
                            doc = doc.querySelectorAll('iframe,frame')[index]?.contentDocument;
                            if (!doc) break;
                        }
                        // A frame may have navigated since collection. Never
                        // apply the old document's rules to a different page.
                        if (!doc || doc.URL !== entry.documentUrl) continue;
                        let style = doc.getElementById('__custom_browser_adblock');
                        if (!style) {
                            style = doc.createElement('style');
                            style.id = '__custom_browser_adblock';
                            (doc.head || doc.documentElement).appendChild(style);
                        }
                        style.textContent = entry.css;
                    } catch (error) { /* cross-origin or removed frame */ }
                }
            })();""".replace("STYLES", json.dumps(styles))
            tab.page().runJavaScript(script, 1)

        # Collect from the main frame and every same-origin subframe so
        # cosmetic rules also apply inside iframes. Cross-origin frames cannot
        # be inspected; that limitation is reported rather than worked around.
        script = """(() => {
            const documents = [];
            const collect = (doc, path, parentUrl) => {
                if (documents.length >= 32 || path.length > 8) return;
                // about:blank/srcdoc inherit their containing page's origin.
                const url = doc.URL.startsWith('about:') ? parentUrl : doc.URL;
                const elements = [...doc.querySelectorAll('[class],[id]')].slice(0, 3000);
                documents.push({path, url, documentUrl:doc.URL,
                    classes:[...new Set(elements.flatMap(e => [...e.classList]))],
                    ids:elements.map(e => e.id).filter(Boolean)});
                [...doc.querySelectorAll('iframe,frame')].forEach((frame, index) => {
                    try {
                        if (frame.contentDocument) collect(frame.contentDocument, [...path, index], url);
                    } catch (error) { /* cross-origin: not inspectable */ }
                });
            };
            collect(document, [], document.URL);
            return {documents};
        })();"""
        tab.page().runJavaScript(script, 1, apply)
