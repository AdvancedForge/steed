"""Read Aloud Voice: the desktop's voices (any speech-dispatcher voice) or
your own speech service, and a sample to listen to."""

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from services import speech_service

FROM_LABELS = {
    "system": "The desktop's voices (on this computer)",
    "service": "Your speech service (OpenAI-compatible)",
}
SAMPLE = "This is how Read Aloud sounds. It reads one sentence at a time."


def system_voices(engine_name=None):
    """Names of the desktop's voices for the current language."""
    try:
        from ui.read_aloud import system_engine

        engine = system_engine(engine_name)
        names = [voice.name() for voice in engine.availableVoices()]
        engine.deleteLater()
        return sorted(dict.fromkeys(n for n in names if n))
    except Exception:
        return []


class ReadAloudVoiceDialog(QDialog):
    def __init__(self, window, engine_name=None, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.config = window.config
        self.setWindowTitle("Read Aloud voice")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Read Aloud (Ctrl+Shift+U) reads the selection, or else the page's "
            "article. Choose who reads it."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.source = QComboBox()
        self.source.setAccessibleName("Who reads")
        for key, label in FROM_LABELS.items():
            self.source.addItem(label, key)
        chosen = self.config.get("read_aloud_voice_from", "system")
        self.source.setCurrentIndex(
            list(FROM_LABELS).index(chosen) if chosen in FROM_LABELS else 0
        )
        self.source.currentIndexChanged.connect(self._shown)
        layout.addWidget(self.source)

        # The desktop's voices.
        self.system_note = QLabel(
            "Voices come from speech-dispatcher. More can be added with your "
            "system's packages, such as espeak-ng, RHVoice or a Piper module."
        )
        self.system_note.setWordWrap(True)
        layout.addWidget(self.system_note)
        self.system_voice = QComboBox()
        self.system_voice.setAccessibleName("Desktop voice")
        self.system_voice.addItem("Default voice", "")
        for name in system_voices(engine_name):
            self.system_voice.addItem(name, name)
        kept = str(self.config.get("read_aloud_system_voice", "") or "")
        found = self.system_voice.findData(kept)
        self.system_voice.setCurrentIndex(max(0, found))
        layout.addWidget(self.system_voice)

        # Your speech service.
        service, model, voice = speech_service.settings(self.config)
        self.service_note = QLabel(
            "The text being read is sent to this service, a sentence or so at a "
            "time. Works with Kokoro-FastAPI, LocalAI or openedai-speech on this "
            "computer, or a hosted service such as OpenAI. Private windows always "
            "use the desktop's voices."
        )
        self.service_note.setWordWrap(True)
        layout.addWidget(self.service_note)
        self.service = QLineEdit(service)
        self.service.setAccessibleName("Speech service address")
        self.service.setPlaceholderText(
            "http://localhost:8880/v1 (Kokoro-FastAPI) or https://…/v1"
        )
        self.model = QLineEdit(model)
        self.model.setAccessibleName("Speech model")
        self.model.setPlaceholderText("Model, for example kokoro or tts-1")
        self.voice = QLineEdit(voice)
        self.voice.setAccessibleName("Speech voice")
        self.voice.setPlaceholderText(
            "Voice, for example af_heart or alloy (blank: the service's default)"
        )
        self.key = QLineEdit()
        self.key.setAccessibleName("Speech service key")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(
            "Key (only for a hosted service; kept in your keyring)"
        )
        self.forget = QPushButton("Forget key")
        self.forget.clicked.connect(self._forget)
        for widget in (self.service, self.model, self.voice, self.key, self.forget):
            layout.addWidget(widget)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        listen = QPushButton("Listen")
        listen.clicked.connect(self.listen)
        save = QPushButton("Save")
        save.setProperty("primary", True)
        save.clicked.connect(self.save)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for button in (listen, save, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._shown()

    def chosen(self):
        return self.source.currentData() or "system"

    def _shown(self, *_):
        service = self.chosen() == "service"
        for widget in (self.system_note, self.system_voice):
            widget.setVisible(not service)
        for widget in (
            self.service_note,
            self.service,
            self.model,
            self.voice,
            self.key,
            self.forget,
        ):
            widget.setVisible(service)
        host = getattr(self, "_html_host", None)
        if host is not None and hasattr(host, "publish"):
            host.publish()

    def save(self):
        values = {
            "read_aloud_voice_from": self.chosen(),
            "read_aloud_system_voice": self.system_voice.currentData() or "",
        }
        if self.chosen() == "service":
            try:
                values["speech_service"] = speech_service.check_service(
                    self.service.text().strip()
                )
            except ValueError as error:
                self.status.setText(str(error))
                return False
            model = " ".join(self.model.text().split())[:200]
            if not model:
                self.status.setText("Name the model the service should use.")
                return False
            values["speech_model"] = model
            values["speech_voice"] = " ".join(self.voice.text().split())[:200]
            if self.key.text():
                try:
                    speech_service.set_api_key(self.key.text())
                except Exception:
                    self.status.setText(
                        "The key could not be saved: no desktop keyring is unlocked."
                    )
                    return False
                self.key.clear()
        for name, value in values.items():
            self.config.set(name, value)
        reader = getattr(self.window, "_read_aloud", None)
        if reader is not None:
            reader.voice_changed()
        self.status.setText("Saved.")
        return True

    def _forget(self):
        try:
            speech_service.set_api_key("")
        except Exception:
            self.status.setText("The keyring could not be reached.")
            return
        self.status.setText("The key was removed from your keyring.")

    def listen(self):
        if not self.save():
            return
        self.window.read_aloud_reader().speak(SAMPLE)
        self.status.setText("Listening…")
