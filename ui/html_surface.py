"""Maintained local HTML with a small command bridge and no website authority."""

import json
import re
from pathlib import Path
from secrets import token_hex

from PyQt6 import sip
from PyQt6.QtCore import (
    QEvent,
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
from PyQt6.QtWidgets import QLabel, QPushButton, QStackedLayout, QVBoxLayout, QWidget

from browser import webengine
from browser.config import Config


class _NoNetwork(webengine.RequestFilter):
    def filter(self, request):
        # setHtml uses a data document. All assets are bundled inline; there is
        # deliberately no localhost server, file access, or website cookie jar.
        request.block(request.url.scheme() != "data")


class _LocalPage(webengine.Page):
    loading_document = False

    def navigation_allowed(self, url, kind, main_frame):
        if main_frame and self.loading_document and url.scheme() == "data":
            self.loading_document = False
            return True
        return False

    def new_window(self, kind):
        return None

    def choose_files(self, several, old_files, accepted_types):
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

    def __init__(self, asset, parent=None, *, label="agent", command_limit=48000):
        super().__init__(parent)
        self.asset = asset
        self.label = label
        self.command_limit = command_limit
        self.ready = False
        self.disposed = False
        self.transparent = False
        self._painted = None  # The page's own background, once it has drawn.
        self.generation = ""
        self._state = {}
        self._last_payload = None
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.fallback = QWidget(self)
        layout = QVBoxLayout(self.fallback)
        layout.addStretch()
        self.notice = QLabel("Loading…", self.fallback)
        self.notice.setWordWrap(True)
        self.notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice.setStyleSheet("color: #8a93a6; font-size: 13px;")
        layout.addWidget(self.notice)
        self.retry = QPushButton("Reload interface", self.fallback)
        self.retry.clicked.connect(self.reload)
        self.retry.hide()
        layout.addWidget(self.retry, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self.stack.addWidget(self.fallback)
        self.view = webengine.View(self)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.setAccessibleName(f"Steed {label} interface")
        self.stack.addWidget(self.view)
        # Whatever resizes the page behind the surface's back, it fills it.
        self.view.installEventFilter(self)
        self.profile = webengine.private_profile(self)
        self.interceptor = _NoNetwork(self.profile)
        webengine.set_request_filter(self.profile, self.interceptor)
        webengine.on_download(self.profile, lambda download: download.cancel())
        self.page = _LocalPage(self.profile, self.view)
        self.view.setPage(self.page)
        settings = self.page.settings()
        webengine.set_options(
            settings,
            local_files_read_files=False,
            local_files_reach_web=False,
            scripts_open_windows=False,
            scripts_use_clipboard=False,
            scripts_paste=False,
            plugins=False,
            fullscreen=False,
            webgl=False,
            pdf_viewer=False,
        )
        webengine.on_permission(
            self.page,
            lambda permission: permission.deny(),
            lambda page, origin, feature: webengine.answer_legacy_permission(
                page, origin, feature, False
            ),
        )
        self.page.fileSystemAccessRequested.connect(lambda request: request.reject())
        self.page.setAudioMuted(True)
        self.channel = webengine.Channel(self.page)
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
        self.notice.setText("Loading…")
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
            # Lumen: how much motion and light (Settings → View) for CSS,
            # and the shared pointer light.
            from ui.lumen import level

            html = (
                html.replace(
                    '<html lang="en"',
                    f'<html lang="en" data-effects="{level(Config())}"',
                    1,
                )
                .replace(
                    # After the page's own styles, so Lumen's rules win ties.
                    "</style>",
                    (assets / "lumen.css").read_text(encoding="utf-8") + "</style>",
                    1,
                )
                .replace(
                    "</body>",
                    '<script nonce="{{NONCE}}">'
                    + (assets / "lumen.js").read_text(encoding="utf-8")
                    + "</script></body>",
                    1,
                )
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
        if self.disposed or not self.ready or len(raw) > self.command_limit:
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
            from ui.lumen import level

            state["effects"] = level(getattr(owner, "config", None) or Config())
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

        webengine.run_in(
            self.page,
            "(() => { document.documentElement.dataset.effects = "
            + json.dumps(state.get("effects", "full"))
            + "; let s = document.getElementById('steed-root-overrides');"
            "if (!s) { s = document.createElement('style'); s.id = 'steed-root-overrides';"
            "s.nonce = document.querySelector('style[nonce]').nonce;"
            "document.head.append(s); } s.textContent = "
            + json.dumps(state.get("root_css", ""), ensure_ascii=True)
            + "; })()",
        )

        def rendered(ok):
            if (
                not self.disposed
                and self.ready
                and generation == self.generation
                and payload == self._last_payload
            ):
                if ok:
                    self.stack.setCurrentWidget(self.view)
                    if not self.transparent:
                        # Match the colour the page actually paints (styles
                        # set it, not only dark mode): a resize then never
                        # shows a light strip before the page catches up.
                        webengine.run_in(
                            self.page,
                            "getComputedStyle(document.body).backgroundColor",
                            self._match_background,
                        )
                else:
                    self._failed()

        dark = state.get("dark", False)
        # The placeholder wears the page's colour, never a grey flash.
        self.fallback.setStyleSheet(
            "background: %s;" % ("#171717" if dark else "#f5f7fb")
        )
        self.page.setBackgroundColor(
            QColor(0, 0, 0, 0)
            if self.transparent  # The host draws rounded, see-through glass.
            else self._painted or QColor("#151b25" if dark else "#f5f7fb")
        )
        webengine.run_in(
            self.page, f"window.steedUI && window.steedUI.render({payload})", rendered
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Moving the agents panel from the sidebar into a tab can leave the
        # layout believing it is current while the page keeps its docked
        # size, showing a strip of background; give it the whole area.
        self.fit()

    def fit(self):
        """Give the page (and its placeholder) the whole surface."""
        self.stack.setGeometry(self.rect())
        for index in range(self.stack.count()):
            widget = self.stack.widget(index)
            if widget is not None and widget.geometry() != self.rect():
                widget.setGeometry(self.rect())

    def eventFilter(self, watched, event):
        if (
            watched is self.view
            and event.type() == QEvent.Type.Resize
            and self.view.size() != self.size()
            and not self.disposed
        ):
            QTimer.singleShot(0, lambda: None if sip.isdeleted(self) else self.fit())
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: None if sip.isdeleted(self) else self.fit())

    def _match_background(self, value):
        found = re.fullmatch(
            r"rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)", str(value or "")
        )
        if self.disposed or found is None or found[4] not in (None, "1"):
            return
        self._painted = QColor(*(int(found[i]) for i in (1, 2, 3)))
        self.page.setBackgroundColor(self._painted)

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
        self._state = {}
        self._last_payload = None
        self.ready = False
        self.timeout.stop()
        self.page.setWebChannel(None)
        sip.delete(self.view)
        sip.delete(self.profile)
