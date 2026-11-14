"""Dictation: speech to text, on this computer or through a service you choose.

- On this computer: open models run by sherpa-onnx in dictation_worker.py (a
  separate process). Models are downloaded once, when chosen, into
  <config>/dictation-models/. Nothing leaves the computer.
- Online: any service that takes OpenAI's /audio/transcriptions request
  (OpenAI itself, Groq, LocalAI, whisper.cpp's server, Speaches…). Its API key
  is kept in the desktop keyring (Secret Service), never in Steed's files.

Audio is held in memory only while you speak and while it is transcribed.
"""

import base64
import json
import shutil
import sys
import tarfile
import tempfile
import threading
from pathlib import Path

from PyQt6.QtCore import (
    QByteArray,
    QObject,
    QProcess,
    QUrl,
    pyqtSignal,
)

ENGINES = ("local", "online", "off")
MODELS_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
# key -> label, what it is good at, archive, download size (MB), loader, language
MODELS = {
    "moonshine-base": {
        "label": "Balanced — Moonshine base (English)",
        "archive": "sherpa-onnx-moonshine-base-en-quantized-2026-02-27",
        "size": 111,
        "kind": "moonshine_v2",
    },
    "moonshine-tiny": {
        "label": "Fastest — Moonshine tiny (English)",
        "archive": "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27",
        "size": 30,
        "kind": "moonshine_v2",
    },
    "whisper-base": {
        "label": "Many languages — Whisper base",
        "archive": "sherpa-onnx-whisper-base",
        "size": 208,
        "kind": "whisper",
    },
    "parakeet-v3": {
        "label": "Most accurate — Parakeet 0.6B (25 European languages)",
        "archive": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        "size": 487,
        "kind": "transducer",
    },
}
DEFAULT_MODEL = "moonshine-base"
DEFAULT_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_ONLINE_MODEL = "gpt-4o-mini-transcribe"
MAX_SECONDS = 120
KEYRING_ATTRIBUTES = {"application": "steed", "purpose": "dictation-api-key"}

# The engine's own speech recognition has no service behind it, and starting it
# ends the tab. Websites get the answer a browser without one gives instead.
SPEECH_GUARD = r"""
(function () {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition || !Recognition.prototype) return;
  const message = "Speech recognition is not available here. Use Steed's " +
    "dictation: Ctrl+Shift+Space.";
  Recognition.prototype.start = function () {
    const recognition = this;
    setTimeout(function () {
      let event;
      try {
        event = new SpeechRecognitionErrorEvent("error",
          {error: "service-not-allowed", message: message});
      } catch (error) {
        event = new Event("error");
        Object.defineProperty(event, "error", {value: "service-not-allowed"});
        Object.defineProperty(event, "message", {value: message});
      }
      recognition.dispatchEvent(event);
      recognition.dispatchEvent(new Event("end"));
    }, 0);
  };
  Recognition.prototype.stop = Recognition.prototype.abort = function () {};
})();
"""


def settings(config):
    """The dictation settings, checked (unknown values fall back)."""
    engine = config.get("dictation_engine", "local")
    model = config.get("dictation_model", DEFAULT_MODEL)
    url = config.get("dictation_url", DEFAULT_URL)
    online_model = config.get("dictation_online_model", DEFAULT_ONLINE_MODEL)
    language = config.get("dictation_language", "")
    return {
        "engine": engine if engine in ENGINES else "local",
        "model": model if model in MODELS else DEFAULT_MODEL,
        "url": url
        if isinstance(url, str) and url.startswith(("https://", "http://"))
        else DEFAULT_URL,
        "online_model": online_model
        if isinstance(online_model, str) and online_model
        else DEFAULT_ONLINE_MODEL,
        "language": language
        if isinstance(language, str) and len(language) <= 8
        else "",
        "live": config.get("dictation_live", True) is not False,
    }


def check_url(url):
    """Online services must use https, except on this computer."""
    found = QUrl(url)
    if (
        not found.isValid()
        or found.scheme() not in ("https", "http")
        or not found.host()
    ):
        raise ValueError("Enter the service's full address, starting with https://")
    if found.scheme() == "http" and found.host() not in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        raise ValueError(
            "Use https:// (plain http only for a service on this computer)"
        )
    return found.toString()


# ---------- API key in the keyring ----------


def api_key():
    try:
        import secretstorage

        connection = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(connection)
        if collection.is_locked():
            collection.unlock()
        items = list(collection.search_items(KEYRING_ATTRIBUTES))
        if not items:
            return ""
        if items[0].is_locked():
            items[0].unlock()
        return items[0].get_secret().decode()
    except Exception:
        return ""


def set_api_key(value):
    """Keep (or with "" forget) the service's key in the keyring."""
    import secretstorage

    connection = secretstorage.dbus_init()
    collection = secretstorage.get_default_collection(connection)
    if collection.is_locked():
        collection.unlock()
    for item in collection.search_items(KEYRING_ATTRIBUTES):
        item.delete()
    if value:
        collection.create_item(
            "Steed dictation service key",
            KEYRING_ATTRIBUTES,
            value.encode(),
            replace=True,
        )


# ---------- Models ----------


def local_available():
    """The on-device engine's library is installed."""
    import importlib.util

    return importlib.util.find_spec("sherpa_onnx") is not None


class Models(QObject):
    """Downloaded models and downloads in progress."""

    progress = pyqtSignal(str, int)  # model, percent
    finished = pyqtSignal(str, str)  # model, error ("" when ready)
    unpacked = pyqtSignal(str, str)  # From the unpacking thread.

    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.folder = Path(folder)
        self.network = None
        self.replies = {}
        self.unpacked.connect(self._unpacked)

    def path(self, model):
        return self.folder / MODELS[model]["archive"]

    def installed(self, model):
        directory = self.path(model)
        return directory.is_dir() and any(directory.iterdir())

    def downloading(self, model):
        return model in self.replies

    def download(self, model):
        from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest

        if model not in MODELS or self.downloading(model) or self.installed(model):
            return
        if self.network is None:
            self.network = QNetworkAccessManager(self)
        self.folder.mkdir(parents=True, exist_ok=True)
        partial = tempfile.NamedTemporaryFile(
            dir=self.folder, prefix=".download-", suffix=".tar.bz2", delete=False
        )
        request = QNetworkRequest(
            QUrl(MODELS_URL + MODELS[model]["archive"] + ".tar.bz2")
        )
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        reply = self.network.get(request)
        self.replies[model] = (reply, partial)
        limit = (MODELS[model]["size"] + 50) << 20

        def received():
            partial.write(bytes(reply.readAll()))
            if partial.tell() > limit:
                reply.abort()

        reply.readyRead.connect(received)
        reply.downloadProgress.connect(
            lambda done, total: (
                total > 0 and self.progress.emit(model, int(done * 100 / total))
            )
        )
        reply.finished.connect(lambda: self._downloaded(model))

    def _downloaded(self, model):
        from PyQt6.QtNetwork import QNetworkReply

        reply, partial = self.replies[model]
        partial.write(bytes(reply.readAll()))
        partial.close()
        ok = reply.error() == QNetworkReply.NetworkError.NoError
        reply.deleteLater()
        if not ok:
            Path(partial.name).unlink(missing_ok=True)
            self.replies.pop(model, None)
            self.finished.emit(model, "The model could not be downloaded.")
            return
        # Unpacking takes a few seconds: not on the Qt thread.
        threading.Thread(
            target=self._unpack, args=(model, Path(partial.name)), daemon=True
        ).start()

    def _unpack(self, model, archive):
        error = ""
        staging = Path(tempfile.mkdtemp(dir=self.folder, prefix=".unpack-"))
        try:
            with tarfile.open(archive, "r:bz2") as bundle:
                bundle.extractall(staging, filter="data")  # No links or escapes.
            found = staging / MODELS[model]["archive"]
            if not found.is_dir():
                raise OSError("Unexpected archive")
            target = self.path(model)
            if target.exists():
                shutil.rmtree(target)
            found.rename(target)
            shutil.rmtree(target / "test_wavs", ignore_errors=True)
        except (OSError, tarfile.TarError, EOFError) as problem:
            error = f"The model could not be unpacked ({type(problem).__name__})."
        finally:
            archive.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
        self.unpacked.emit(model, error)  # Queued: back on the Qt thread.

    def _unpacked(self, model, error):
        self.replies.pop(model, None)
        self.finished.emit(model, error)

    def remove(self, model):
        shutil.rmtree(self.path(model), ignore_errors=True)


# ---------- Engines ----------


class LocalEngine(QObject):
    """The model in dictation_worker.py; one request at a time, in order."""

    result = pyqtSignal(int, str)  # request id, text
    failed = pyqtSignal(str)
    WORKER = Path(__file__).resolve().parents[1] / "dictation_worker.py"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.loaded = None
        self.buffer = bytearray()
        self.waiting = []  # (id, line) not yet sent
        self.busy = None

    def ensure(self, directory, kind, language=""):
        key = (str(directory), kind, language)
        if self.loaded == key and self.process is not None:
            return
        self.stop()
        self.process = QProcess(self)
        self.process.setProgram(sys.executable)
        self.process.setArguments([str(self.WORKER)])
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self.process.readAllStandardError)
        self.process.finished.connect(self._ended)
        self.process.start()
        self.loaded = key
        self.busy = "load"
        self._send(
            {
                "op": "load",
                "kind": kind,
                "dir": str(directory),
                "threads": 4,
                "language": language,
            }
        )

    def transcribe(self, request_id, audio, rate, sample_format, channels):
        line = {
            "op": "decode",
            "id": request_id,
            "rate": rate,
            "format": sample_format,
            "channels": channels,
            "audio": base64.b64encode(audio).decode("ascii"),
        }
        self.waiting.append((request_id, line))
        self._next()

    def pending(self):
        return len(self.waiting) + (self.busy is not None)

    def _next(self):
        if self.busy is None and self.waiting and self.process is not None:
            request_id, line = self.waiting.pop(0)
            self.busy = request_id
            self._send(line)

    def _send(self, value):
        self.process.write(json.dumps(value).encode() + b"\n")

    def _read(self):
        self.buffer.extend(bytes(self.process.readAllStandardOutput()))
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            try:
                value = json.loads(raw)
            except ValueError:
                continue
            if self.busy == "load" and "error" in value:
                self.loaded = None  # Try loading again next time.
            self.busy = None
            if "error" in value:
                self.failed.emit(value["error"])
            elif value.get("loaded"):
                pass
            elif isinstance(value.get("id"), int):
                self.result.emit(value["id"], str(value.get("text", "")))
            self._next()

    def _ended(self, *_):
        if self.process is not None:
            self.process = None
            self.loaded = None
            if self.busy is not None or self.waiting:
                self.waiting.clear()
                self.busy = None
                self.failed.emit("The dictation engine stopped.")

    def stop(self):
        process, self.process = self.process, None
        self.loaded = None
        self.waiting.clear()
        self.busy = None
        if process is not None:
            process.finished.disconnect()
            process.kill()
            process.waitForFinished(1000)
            process.deleteLater()


class OnlineEngine(QObject):
    """An OpenAI-style /audio/transcriptions service."""

    result = pyqtSignal(int, str)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = None
        self.busy = 0

    def pending(self):
        return self.busy

    def transcribe(
        self, request_id, audio, rate, sample_format, channels, url, model, key
    ):
        from PyQt6.QtNetwork import (
            QHttpMultiPart,
            QHttpPart,
            QNetworkAccessManager,
            QNetworkReply,
            QNetworkRequest,
        )

        if self.network is None:
            self.network = QNetworkAccessManager(self)
        parts = QHttpMultiPart(QHttpMultiPart.ContentType.FormDataType)
        audio_part = QHttpPart()
        audio_part.setHeader(
            QNetworkRequest.KnownHeaders.ContentDispositionHeader,
            'form-data; name="file"; filename="dictation.wav"',
        )
        audio_part.setHeader(
            QNetworkRequest.KnownHeaders.ContentTypeHeader, "audio/wav"
        )
        audio_part.setBody(QByteArray(wav(audio, rate, sample_format, channels)))
        model_part = QHttpPart()
        model_part.setHeader(
            QNetworkRequest.KnownHeaders.ContentDispositionHeader,
            'form-data; name="model"',
        )
        model_part.setBody(model.encode())
        parts.append(audio_part)
        parts.append(model_part)
        request = QNetworkRequest(QUrl(url))
        if key:
            request.setRawHeader(b"Authorization", b"Bearer " + key.encode())
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setTransferTimeout(60000)
        reply = self.network.post(request, parts)
        parts.setParent(reply)
        self.busy += 1

        def done():
            self.busy -= 1
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            body = bytes(reply.readAll())
            ok = reply.error() == QNetworkReply.NetworkError.NoError
            reply.deleteLater()
            try:
                text = json.loads(body).get("text") if ok else None
            except (ValueError, AttributeError):
                text = None
            if isinstance(text, str):
                self.result.emit(request_id, text.strip())
            elif status in (401, 403):
                self.failed.emit("The dictation service refused the key.")
            else:
                self.failed.emit(
                    "The dictation service did not answer"
                    + (f" (HTTP {status})." if status else ".")
                )

        reply.finished.connect(done)

    def stop(self):
        pass


def wav(audio, rate, sample_format, channels):
    """Recorded audio as a WAV file (16-bit, first channel)."""
    import array
    import struct

    if sample_format == "float32":
        values = array.array("f", audio[: len(audio) - len(audio) % 4])
        pcm = array.array(
            "h", (max(-32768, min(32767, int(v * 32767))) for v in values[::channels])
        )
    else:
        values = array.array("h", audio[: len(audio) - len(audio) % 2])
        pcm = values[::channels] if channels > 1 else values
    data = pcm.tobytes()
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


# ---------- The microphone ----------


class Recorder(QObject):
    """The default microphone, while held; audio stays in memory."""

    level = pyqtSignal(float)  # 0..1, for the listening pill
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.device = None
        self.data = bytearray()
        self.rate, self.format, self.channels = 16000, "int16", 1

    def start(self):
        from PyQt6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            self.failed.emit("No microphone was found.")
            return False
        wanted = QAudioFormat()
        wanted.setSampleRate(16000)
        wanted.setChannelCount(1)
        wanted.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        chosen = (
            wanted if device.isFormatSupported(wanted) else device.preferredFormat()
        )
        if chosen.sampleFormat() not in (
            QAudioFormat.SampleFormat.Int16,
            QAudioFormat.SampleFormat.Float,
        ):
            chosen.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.rate = chosen.sampleRate()
        self.channels = max(1, chosen.channelCount())
        self.format = (
            "float32"
            if chosen.sampleFormat() == QAudioFormat.SampleFormat.Float
            else "int16"
        )
        self.data = bytearray()
        self.source = QAudioSource(device, chosen, self)
        self.device = self.source.start()
        if self.device is None:
            self.failed.emit("The microphone could not be opened.")
            self.source = None
            return False
        self.device.readyRead.connect(self._read)
        return True

    def bytes_per_second(self):
        return self.rate * self.channels * (4 if self.format == "float32" else 2)

    def _read(self):
        if self.device is None:
            return
        chunk = bytes(self.device.readAll())
        if len(self.data) + len(chunk) > self.bytes_per_second() * MAX_SECONDS:
            return
        self.data.extend(chunk)
        self.level.emit(peak(chunk, self.format))

    def snapshot(self):
        return bytes(self.data)

    def stop(self):
        if self.device is not None:
            self._read()
        if self.source is not None:
            self.source.stop()
            self.source.deleteLater()
        self.source = self.device = None
        audio, self.data = bytes(self.data), bytearray()
        return audio


def peak(chunk, sample_format):
    import array

    if not chunk:
        return 0.0
    if sample_format == "float32":
        values = array.array("f", chunk[: len(chunk) - len(chunk) % 4])
        return min(1.0, max((abs(v) for v in values), default=0.0))
    values = array.array("h", chunk[: len(chunk) - len(chunk) % 2])
    return min(1.0, max((abs(v) for v in values), default=0) / 32768)


# ---------- Spoken commands and spacing ----------


def tidy(text):
    """Spoken "new line" / "new paragraph" become line breaks."""
    import re

    text = text.strip()
    text = re.sub(r"\s*\bnew paragraph\b[,.]?\s*", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\bnew line\b[,.]?\s*", "\n", text, flags=re.IGNORECASE)
    return text.strip(" ")


# ---------- One of each for the whole app ----------

_shared = {}


def shared(kind, folder=None):
    """The app's local engine, online engine or model store (one microphone,
    one loaded model, however many windows)."""
    from PyQt6.QtCore import QCoreApplication

    if kind not in _shared:
        app = QCoreApplication.instance()
        made = {"local": LocalEngine, "online": OnlineEngine}.get(kind)
        _shared[kind] = made(app) if made else Models(folder, app)
        _shared[kind].destroyed.connect(lambda *_: _shared.pop(kind, None))
    return _shared[kind]


def stop_shared():
    """Steed is closing: end the worker process."""
    engine = _shared.get("local")
    if engine is not None:
        engine.stop()
