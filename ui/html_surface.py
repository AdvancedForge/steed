"""Maintained local HTML with a small command bridge and no website authority."""

import json
from pathlib import Path
from secrets import token_hex

from PyQt6 import sip
from PyQt6.QtCore import (
    QFile,
    QIODevice,
    QObject,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QLabel, QPushButton, QStackedLayout, QVBoxLayout, QWidget


class _NoNetwork(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        # setHtml uses a data document. All assets are bundled inline; there is
        # deliberately no localhost server, file access, or website cookie jar.
        info.block(info.requestUrl().scheme() != "data")


class _LocalPage(QWebEnginePage):
    loading_document = False

    def acceptNavigationRequest(self, url, kind, main_frame):
        if main_frame and self.loading_document and url.scheme() == "data":
            self.loading_document = False
            return True
        return False

    def createWindow(self, kind):
        return None

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        return []


class _Bridge(QObject):
    ready_received = pyqtSignal(str)
    command_received = pyqtSignal(str)

    @pyqtSlot(str)
    def ready(self, generation):
        self.ready_received.emit(generation)

    @pyqtSlot(str)
    def dispatch(self, command):
        self.command_received.emit(command)


class HtmlSurface(QWidget):
    """Only repository-owned assets may be loaded into this privileged surface."""

    command = pyqtSignal(dict)
    reloading = pyqtSignal()

    def __init__(self, asset, parent=None, *, label="agent"):
        super().__init__(parent)
        self.asset = asset
        self.label = label
        self.ready = False
        self.disposed = False
        self.generation = ""
        self._state = {}
        self._last_payload = None
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.fallback = QWidget(self)
        layout = QVBoxLayout(self.fallback)
        layout.addStretch()
        self.notice = QLabel(f"Opening {label} interface…", self.fallback)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.retry = QPushButton("Reload interface", self.fallback)
        self.retry.clicked.connect(self.reload)
        self.retry.hide()
        layout.addWidget(self.retry)
        layout.addStretch()
        self.stack.addWidget(self.fallback)
        self.view = QWebEngineView(self)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.setAccessibleName(f"Steed {label} interface")
        self.stack.addWidget(self.view)
        self.profile = QWebEngineProfile(self)
        self.interceptor = _NoNetwork(self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.profile.downloadRequested.connect(lambda download: download.cancel())
        self.page = _LocalPage(self.profile, self.view)
        self.view.setPage(self.page)
        settings = self.page.settings()
        for attribute in (
            "LocalContentCanAccessFileUrls",
            "LocalContentCanAccessRemoteUrls",
            "JavascriptCanOpenWindows",
            "JavascriptCanAccessClipboard",
            "JavascriptCanPaste",
            "PluginsEnabled",
            "FullScreenSupportEnabled",
            "WebGLEnabled",
            "PdfViewerEnabled",
        ):
            settings.setAttribute(
                getattr(QWebEngineSettings.WebAttribute, attribute), False
            )
        self.page.permissionRequested.connect(lambda permission: permission.deny())
        self.page.fileSystemAccessRequested.connect(lambda request: request.reject())
        self.page.setAudioMuted(True)
        self.channel = QWebChannel(self.page)
        self.bridge = _Bridge(self.channel)
        self.channel.registerObject("interface", self.bridge)
        self.page.setWebChannel(self.channel)
        self.bridge.ready_received.connect(self._ready)
        # A command can close its view. Leave the bridge's own slot before a
        # controller is allowed to destroy the page/channel that owns it.
        self.bridge.command_received.connect(
            self._dispatch, Qt.ConnectionType.QueuedConnection
        )
        self.page.renderProcessTerminated.connect(lambda *_: self._failed())
        self.page.loadFinished.connect(self._loaded)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self._failed)
        self.reload()

    def reload(self):
        if self.disposed:
            return
        self.ready = False
        self.generation = token_hex(16)
        self._last_payload = None
        self.reloading.emit()
        self.stack.setCurrentWidget(self.fallback)
        self.notice.setText(f"Opening {self.label} interface…")
        self.retry.hide()
        assets = Path(__file__).parent / "web"
        library = QFile(":/qtwebchannel/qwebchannel.js")
        if not library.open(QIODevice.OpenModeFlag.ReadOnly):
            self._failed()
            return
        channel_script = bytes(library.readAll()).decode("utf-8")
        library.close()
        nonce = token_hex(16)
        try:
            html = (assets / f"{self.asset}.html").read_text(encoding="utf-8")
            html = html.replace(
                "{{STYLE}}",
                (assets / "root.css").read_text(encoding="utf-8")
                + (assets / "theme.css").read_text(encoding="utf-8"),
            )
            extra_style = assets / f"{self.asset}.css"
            if extra_style.exists():
                # Separate maintained screens share tokens without sharing
                # screen-specific layouts. No file or URL is supplied by HTML.
                html = html.replace(
                    "{{EXTRA_STYLE}}", extra_style.read_text(encoding="utf-8")
                )
            html = html.replace(
                "{{SCRIPT}}", (assets / f"{self.asset}.js").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError):
            self._failed()
            return
        html = html.replace("{{CHANNEL}}", channel_script)
        html = html.replace("{{GENERATION}}", self.generation).replace(
            "{{NONCE}}", nonce
        )
        self.page.loading_document = True
        self.timeout.start(15000)
        self.page.setHtml(html, QUrl("about:blank"))

    def _loaded(self, ok):
        if not ok:
            self._failed()

    def _ready(self, generation):
        if self.disposed or generation != self.generation:
            return
        self.ready = True
        self.timeout.stop()
        self.publish(self._state, force=True)

    def _dispatch(self, raw):
        if self.disposed or not self.ready or len(raw) > 48000:
            return
        try:
            command = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            return
        if (
            not isinstance(command, dict)
            or command.pop("generation", None) != self.generation
        ):
            return
        self.command.emit(command)

    def publish(self, state, *, force=False):
        state = dict(state)
        owner = self.parentWidget()
        while owner is not None and not hasattr(owner, "profile_manager"):
            owner = owner.parentWidget()
        if owner is not None:
            manager = owner.profile_manager
            style = manager.registry.page_style
            profile = manager.current_profile_name
            state["root_css"] = style.css(profile)
            # Settings may preview its unsaved global dark-mode preference.
            if style.get(profile)["theme"] != "system" or "dark" not in state:
                state["dark"] = style.dark(profile)
        self._state = state
        if not self.ready or self.disposed:
            return
        payload = json.dumps(state, ensure_ascii=True)
        if not force and payload == self._last_payload:
            return
        self._last_payload = payload
        generation = self.generation

        self.page.runJavaScript(
            "(() => { let s = document.getElementById('steed-root-overrides');"
            "if (!s) { s = document.createElement('style'); s.id = 'steed-root-overrides';"
            "s.nonce = document.querySelector('style[nonce]').nonce;"
            "document.head.append(s); } s.textContent = "
            + json.dumps(state.get("root_css", ""), ensure_ascii=True)
            + "; })()"
        )

        def rendered(ok):
            if (
                not self.disposed
                and self.ready
                and generation == self.generation
                and payload == self._last_payload
                and ok
            ):
                self.stack.setCurrentWidget(self.view)

        dark = state.get("dark", False)
        self.page.setBackgroundColor(QColor("#151b25" if dark else "#f5f7fb"))
        self.page.runJavaScript(
            f"window.steedUI && window.steedUI.render({payload})", rendered
        )

    def conceal(self):
        """Cover stale pixels synchronously before publishing a private state."""
        if not self.disposed:
            self.stack.setCurrentWidget(self.fallback)
            self.notice.setText("Switching profile…")
            self.retry.hide()

    def _failed(self):
        if self.disposed:
            return
        self.ready = False
        self.timeout.stop()
        self.stack.setCurrentWidget(self.fallback)
        self.notice.setText(
            f"The {self.label} interface could not load. Browsing and running tasks are unaffected. "
            "Reload the interface to reconnect; sent messages are not replayed."
        )
        self.retry.show()

    def dispose(self):
        """Destroy the page before its private engine profile, on the Qt thread."""
        if self.disposed:
            return
        self.disposed = True
        self.ready = False
        self.timeout.stop()
        self.page.setWebChannel(None)
        sip.delete(self.view)
        sip.delete(self.profile)
