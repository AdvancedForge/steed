"""Native filtering with asynchronous last-known-good list updates."""

import json
import threading

import adblock
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

from utils import load_json, save_json

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
        filters = adblock.FilterSet(debug=True)
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

    def interceptRequest(self, info):
        source = info.firstPartyUrl().toString()
        if info.requestUrl().scheme() not in (
            "http",
            "https",
        ) or not self.service.applies(source):
            return
        resource = info.resourceType().name.removeprefix("ResourceType").lower()
        resource = {
            "mainframe": "document",
            "subframe": "subdocument",
            "xhr": "xmlhttprequest",
        }.get(resource, resource)
        if self.service.engine.blocked(info.requestUrl().toString(), source, resource):
            info.block(True)
            self.blocked.emit(info.requestUrl().host())


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
        self.engine = FilterEngine(cached if valid else BOOTSTRAP)
        self.status = (
            "Cached filter lists loaded"
            if valid
            else "Bootstrap rules only; update filter lists for full coverage"
        )
        self.count = 0
        self.network = QNetworkAccessManager(self)
        self.replies = []
        self.payloads = []
        self.updating = False
        self.compiled.connect(self.install)
        self.compilation_failed.connect(self.fail)

    def attach(self, profile):
        interceptor = FilterInterceptor(self, profile)
        interceptor.blocked.connect(self.note_blocked)
        profile.setUrlRequestInterceptor(interceptor)

    def applies(self, url):
        host = QUrl(url).host().lower()
        exceptions = self.config.get("adblock_exceptions", [])
        exceptions = (
            [site for site in exceptions if isinstance(site, str)]
            if isinstance(exceptions, list)
            else []
        )
        return bool(
            self.config.get("adblock_enabled", True)
            and host
            and not any(
                host == site or host.endswith("." + site) for site in exceptions
            )
        )

    def note_blocked(self, host):
        self.count += 1
        self.changed.emit()

    def update(self):
        if self.updating:
            return
        self.updating = True
        self.payloads = []
        self.status = "Downloading filter lists..."
        self.changed.emit()
        self.fetch(0)

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

            def compile_rules():
                try:
                    self.compiled.emit(FilterEngine(rules), rules)
                except Exception as error:
                    try:
                        self.compilation_failed.emit(str(error))
                    except RuntimeError:
                        pass  # Application was closed while compilation ran.

            threading.Thread(target=compile_rules, daemon=True).start()
            return
        request = QNetworkRequest(QUrl(SOURCES[index]))
        request.setTransferTimeout(20_000)
        reply = self.network.get(request)
        self.replies.append(reply)
        reply.downloadProgress.connect(
            lambda received, total: reply.abort() if received > 10_000_000 else None
        )

        def finished():
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
                or not self.applies(url)
                or not isinstance(data, dict)
            ):
                return
            selectors = self.engine.selectors(
                url, data.get("classes", []), data.get("ids", [])
            )
            css = "\n".join(
                selector + " {display:none!important;}" for selector in selectors
            )
            script = """(() => {
                let style = document.getElementById('__custom_browser_adblock');
                if (!style) { style = document.createElement('style'); style.id = '__custom_browser_adblock'; (document.head || document.documentElement).appendChild(style); }
                style.textContent = CSS;
            })();""".replace("CSS", json.dumps(css))
            tab.page().runJavaScript(script, 1)

        tab.page().runJavaScript(
            """(() => {
            const elements = [...document.querySelectorAll('[class],[id]')].slice(0, 3000);
            return {classes:[...new Set(elements.flatMap(e => [...e.classList]))], ids:elements.map(e => e.id).filter(Boolean)};
        })();""",
            1,
            apply,
        )
