"""Media controls: play/pause and mute from the toolbar, a Mini Player, and the
desktop's media keys and sound menu (MPRIS).

Qt WebEngine turns the web's picture-in-picture off, so the Mini Player moves
the playing tab into a small window that stays on top and fills it with the
video; selecting the tab in the strip brings it back.
"""

import os

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, pyqtClassInfo, pyqtProperty, pyqtSlot
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from browser import webengine

# Steed's own script world: the page can neither see nor change these.
STATE = """(() => {
  const media = [...document.querySelectorAll('video, audio')];
  const playing = media.some(m => !m.paused && !m.ended);
  const meta = navigator.mediaSession && navigator.mediaSession.metadata;
  return JSON.stringify({
    has_media: media.length > 0, playing,
    title: meta ? meta.title : '', artist: meta ? meta.artist : '',
  });
})()"""
# Pause everything playing, or resume the media paused most recently.
TOGGLE = """(() => {
  const media = [...document.querySelectorAll('video, audio')];
  const playing = media.filter(m => !m.paused && !m.ended);
  if (playing.length) { playing.forEach(m => m.pause()); return 'paused'; }
  const paused = media.filter(m => m.paused && m.currentTime > 0)
    .concat(media.filter(m => m.paused));
  if (paused.length) { paused[0].play(); return 'playing'; }
  return 'none';
})()"""
MINI_ON = """(() => {
  const videos = [...document.querySelectorAll('video')];
  const video = videos.find(v => !v.paused) ||
    videos.sort((a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight)[0];
  if (!video) return false;
  video.setAttribute('data-steed-mini', '');
  let style = document.getElementById('steed-mini-style');
  if (!style) {
    style = document.createElement('style'); style.id = 'steed-mini-style';
    style.textContent = '[data-steed-mini]{position:fixed!important;inset:0!important;' +
      'width:100vw!important;height:100vh!important;max-width:none!important;' +
      'max-height:none!important;z-index:2147483647!important;background:#000!important;' +
      'object-fit:contain!important;transform:none!important}' +
      'html,body{overflow:hidden!important}';
    (document.head || document.documentElement).appendChild(style);
  }
  return true;
})()"""
MINI_OFF = """(() => {
  document.querySelectorAll('[data-steed-mini]').forEach(v => v.removeAttribute('data-steed-mini'));
  const style = document.getElementById('steed-mini-style'); if (style) style.remove();
  return true;
})()"""


def run(tab, source, callback=None):
    if tab is not None and not sip.isdeleted(tab):
        webengine.run_script(
            tab.page(),
            source,
            callback or (lambda _: None),
            world=webengine.STEED_WORLD,
        )


class MiniPlayer(QWidget):
    """A small always-on-top window holding one tab's view."""

    def __init__(self, window, tab):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.window = window
        self.tab = tab
        self.setWindowTitle(f"Mini Player · {tab.title() or 'Steed'}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        window.tab_layout.removeWidget(tab)
        tab.setParent(self)
        layout.addWidget(tab)
        tab.show()
        tab.mini_player = self
        self.resize(480, 270)
        screen = window.screen().availableGeometry()
        self.move(screen.right() - 500, screen.bottom() - 300)
        run(tab, MINI_ON)

    def release(self):
        """Give the tab back to its window."""
        tab, self.tab = self.tab, None
        if tab is None or sip.isdeleted(tab):
            return None
        tab.mini_player = None
        run(tab, MINI_OFF)
        if not self.window._closing and tab in self.window.tabs:
            self.layout().removeWidget(tab)
            tab.setParent(self.window.tab_container)
            self.window.tab_layout.addWidget(tab)
            current = self.window.tabs[self.window.current_tab_index]
            tab.setVisible(tab is current)
        return tab

    def closeEvent(self, event):
        self.release()
        super().closeEvent(event)


def mpris_available():
    try:
        from PyQt6.QtDBus import QDBusConnection

        return QDBusConnection.sessionBus().isConnected()
    except ImportError:
        return False


def make_mpris(controller):
    """Register Steed as a desktop media player; returns the object or None.

    controller provides status() -> (playback, title, artist), toggle(),
    play(), pause() and show().
    """
    from PyQt6.QtDBus import (
        QDBusAbstractAdaptor,
        QDBusConnection,
        QDBusMessage,
        QDBusObjectPath,
    )

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None

    @pyqtClassInfo("D-Bus Interface", "org.mpris.MediaPlayer2")
    class Root(QDBusAbstractAdaptor):
        @pyqtProperty(str)
        def Identity(self):
            return "Steed"

        @pyqtProperty(str)
        def DesktopEntry(self):
            return "steed"

        @pyqtProperty(bool)
        def CanQuit(self):
            return False

        @pyqtProperty(bool)
        def CanRaise(self):
            return True

        @pyqtProperty(bool)
        def HasTrackList(self):
            return False

        @pyqtProperty("QStringList")
        def SupportedUriSchemes(self):
            return []

        @pyqtProperty("QStringList")
        def SupportedMimeTypes(self):
            return []

        @pyqtSlot()
        def Raise(self):
            controller.show()

        @pyqtSlot()
        def Quit(self):
            pass

    @pyqtClassInfo("D-Bus Interface", "org.mpris.MediaPlayer2.Player")
    class Player(QDBusAbstractAdaptor):
        @pyqtProperty(str)
        def PlaybackStatus(self):
            return controller.status()[0]

        @pyqtProperty("QVariantMap")
        def Metadata(self):
            return owner.metadata()

        @pyqtProperty(bool)
        def CanPlay(self):
            return True

        @pyqtProperty(bool)
        def CanPause(self):
            return True

        @pyqtProperty(bool)
        def CanControl(self):
            return True

        @pyqtProperty(bool)
        def CanGoNext(self):
            return False

        @pyqtProperty(bool)
        def CanGoPrevious(self):
            return False

        @pyqtProperty(bool)
        def CanSeek(self):
            return False

        @pyqtSlot()
        def PlayPause(self):
            controller.toggle()

        @pyqtSlot()
        def Play(self):
            controller.play()

        @pyqtSlot()
        def Pause(self):
            controller.pause()

        @pyqtSlot()
        def Stop(self):
            controller.pause()

        @pyqtSlot()
        def Next(self):
            pass

        @pyqtSlot()
        def Previous(self):
            pass

    class Mpris(QObject):
        PATH = "/org/mpris/MediaPlayer2"

        def __init__(self):
            super().__init__()
            self.root = Root(self)
            self.player = Player(self)
            # One name per process (the MPRIS way for several instances).
            self.service = f"org.mpris.MediaPlayer2.steed.instance{os.getpid()}"
            self.registered = bus.registerObject(
                self.PATH, self
            ) and bus.registerService(self.service)

        def metadata(self):
            _, title, artist = controller.status()
            data = {
                "mpris:trackid": QDBusObjectPath("/org/steed/track/current"),
                "xesam:title": title or "Steed",
            }
            if artist:
                data["xesam:artist"] = [artist]
            return data

        def changed(self):
            """Tell the desktop that the status or title changed."""
            if not self.registered:
                return
            message = QDBusMessage.createSignal(
                self.PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged"
            )
            message.setArguments(
                [
                    "org.mpris.MediaPlayer2.Player",
                    {
                        "PlaybackStatus": controller.status()[0],
                        "Metadata": self.metadata(),
                    },
                    [],
                ]
            )
            bus.send(message)

        def close(self):
            if self.registered:
                bus.unregisterService(self.service)
                bus.unregisterObject(self.PATH)
                self.registered = False

    owner = Mpris()
    return owner


class MediaController(QObject):
    """The one tab whose media the toolbar, keys and sound menu control."""

    def __init__(self):
        super().__init__()
        self.window = None
        self.tab = None
        self.playing = False
        self.title = ""
        self.artist = ""
        self.mpris = None

    def start_desktop(self):
        if self.mpris is None:
            try:
                self.mpris = make_mpris(self)
            except Exception:  # A desktop without a session bus.
                self.mpris = None

    def alive(self):
        return (
            self.tab is not None
            and not sip.isdeleted(self.tab)
            and self.window is not None
            and not sip.isdeleted(self.window)
            and not self.window._closing
            and self.tab in self.window.tabs
        )

    def update(self, window, tab, state):
        """state: the page's STATE answer (or None when it has no media)."""
        if not state or not state.get("has_media"):
            if tab is self.tab:
                self.forget()
            return
        previous = self.window
        self.window, self.tab = window, tab
        if (
            previous is not None
            and previous is not window
            and not sip.isdeleted(previous)
        ):
            previous._media_changed()
        self.playing = bool(state.get("playing"))
        self.title = str(state.get("title") or tab.title() or "")[:200]
        self.artist = str(state.get("artist") or "")[:200]
        self.changed()

    def forget(self, window=None):
        if window is not None and window is not self.window:
            return
        old = self.window
        self.window = self.tab = None
        self.playing = False
        self.title = self.artist = ""
        if old is not None and not sip.isdeleted(old):
            old._media_changed()
        if self.mpris is not None:
            self.mpris.changed()

    def changed(self):
        if self.window is not None and not sip.isdeleted(self.window):
            self.window._media_changed()
        if self.mpris is not None:
            self.mpris.changed()

    def status(self):
        if not self.alive():
            return "Stopped", "", ""
        return ("Playing" if self.playing else "Paused"), self.title, self.artist

    def refresh_tab(self, window, tab):
        """Ask a tab what it is playing; it becomes the controlled tab."""
        import json

        def answered(raw):
            try:
                state = json.loads(raw) if isinstance(raw, str) else None
            except ValueError:
                state = None
            self.update(window, tab, state)

        run(tab, STATE, answered)

    def refresh(self):
        import json

        if not self.alive():
            return
        window, tab = self.window, self.tab

        def answered(raw):
            try:
                state = json.loads(raw) if isinstance(raw, str) else None
            except ValueError:
                state = None
            self.update(window, tab, state)

        run(tab, STATE, answered)

    def toggle(self):
        if self.alive():
            run(self.tab, TOGGLE, lambda _: self.refresh())

    def play(self):
        if self.alive() and not self.playing:
            self.toggle()

    def pause(self):
        if self.alive() and self.playing:
            self.toggle()

    def show(self):
        if self.alive():
            window = self.window
            window._switch_tab(window.tabs.index(self.tab))
            window.raise_()
            window.activateWindow()


_CONTROLLER = None


def controller():
    global _CONTROLLER
    if _CONTROLLER is None:
        _CONTROLLER = MediaController()
    return _CONTROLLER
