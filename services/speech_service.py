"""Read Aloud with your own speech service instead of the desktop's voices.

Any service that answers OpenAI's /v1/audio/speech request works: Kokoro-
FastAPI, LocalAI or openedai-speech on this computer (nothing leaves it), or a
hosted one such as OpenAI. Steed sends one sentence or so at a time, asking
for the next while the current one plays. A hosted service's key stays in the
desktop keyring. Private windows always use the desktop's voices.
"""

import json

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

KEY_PURPOSE = "speech-api-key"
MAX_AUDIO = 32 * 1024 * 1024
TIMEOUT_MS = 60000
KEPT = 3  # Sentences fetched ahead.


def settings(config):
    """(service address, model, voice) as saved."""
    return tuple(
        str(config.get(name, "") or "").strip()
        for name in ("speech_service", "speech_model", "speech_voice")
    )


def check_service(url):
    """The service's base address (…/v1), or ValueError."""
    from services.dictation import check_url

    return check_url(url).rstrip("/")


def api_key():
    from services import service_keys

    return service_keys.read(KEY_PURPOSE)


def set_api_key(value):
    from services import service_keys

    service_keys.write(KEY_PURPOSE, "Steed speech service key", value)


def speed(rate):
    """Read Aloud's rate (-1 slower … 1 faster) as the service's speed."""
    return round(2 ** max(-1.0, min(1.0, rate)), 2)


class ServiceVoice(QObject):
    """Speaks text with the speech service: finished when a piece has been
    played, failed(message) when the service or playback fails."""

    finished = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, config, rate=0.0, parent=None, *, key=None):
        super().__init__(parent)
        self.service, self.model, self.voice = settings(config)
        if self.service:
            self.service = check_service(self.service)
        self.key = api_key() if key is None else key
        self.rate = rate
        self.network = QNetworkAccessManager(self)
        self.audio = {}  # text -> bytes, fetched ahead
        self.fetching = {}  # text -> reply
        self.current = None
        self.player = None
        self.output = None
        self.buffer = None

    # ------------------------------------------------------------ fetching

    def _fetch(self, text):
        request = QNetworkRequest(QUrl(self.service + "/audio/speech"))
        request.setHeader(
            QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json"
        )
        if self.key:
            request.setRawHeader(b"Authorization", b"Bearer " + self.key.encode())
        request.setTransferTimeout(TIMEOUT_MS)
        body = {
            "model": self.model,
            "input": text,
            "response_format": "wav",
            "speed": speed(self.rate),
        }
        if self.voice:
            body["voice"] = self.voice
        reply = self.network.post(request, json.dumps(body).encode())
        self.fetching[text] = reply
        reply.finished.connect(lambda: self._answered(reply, text))

    def _answered(self, reply, text):
        reply.deleteLater()
        if self.fetching.get(text) is not reply:
            return  # Stopped, or asked again since.
        del self.fetching[text]
        if reply.error() != QNetworkReply.NetworkError.NoError:
            if text == self.current:
                status = reply.attribute(
                    QNetworkRequest.Attribute.HttpStatusCodeAttribute
                )
                self.failed.emit(
                    "Read Aloud: the speech service refused the key."
                    if status in (401, 403)
                    else "Read Aloud: the speech service did not answer "
                    f"({status or reply.errorString()}). Is it running, and "
                    "are the model and voice right?"
                )
            return
        data = bytes(reply.readAll()[:MAX_AUDIO])
        if text == self.current:
            self._play(data)
        elif len(self.audio) < KEPT:
            self.audio[text] = data

    # ------------------------------------------------------------- playing

    def say(self, text, upcoming=None):
        self.current = text
        if not self.service or not self.model:
            self.failed.emit(
                "Read Aloud: set up your speech service first "
                "(View → Read Aloud Voice…)."
            )
            return
        if text in self.audio:
            self._play(self.audio.pop(text))
        elif text not in self.fetching:
            self._fetch(text)
        if upcoming and upcoming not in self.audio and upcoming not in self.fetching:
            self._fetch(upcoming)

    def _play(self, data):
        try:
            from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            self.failed.emit("Read Aloud: playing audio needs Qt Multimedia.")
            return
        if self.player is None:
            self.player = QMediaPlayer(self)
            self.output = QAudioOutput(self)
            self.player.setAudioOutput(self.output)
            self.player.mediaStatusChanged.connect(self._status)
            self.player.errorOccurred.connect(
                lambda *_: self.failed.emit(
                    "Read Aloud: the speech service's audio could not be played."
                )
            )
        previous, self.buffer = self.buffer, QBuffer(self)
        self.buffer.setData(QByteArray(data))
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.player.setSourceDevice(self.buffer, QUrl("speech.wav"))
        if previous is not None:
            previous.deleteLater()
        self.player.play()

    def _status(self, status):
        from PyQt6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.current:
            self.current = None
            self.finished.emit()

    def pause(self):
        if self.player is not None:
            self.player.pause()

    def resume(self):
        if self.player is not None:
            self.player.play()

    def set_rate(self, rate):
        self.rate = rate
        self.audio.clear()  # Fetched at the old speed.

    def stop(self):
        self.current = None
        fetching, self.fetching = self.fetching, {}
        for reply in fetching.values():
            reply.abort()
        self.audio.clear()
        if self.player is not None:
            self.player.stop()
