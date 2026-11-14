"""Dictation Settings: which engine, which model, which service, and a test."""

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from services import dictation

ENGINE_LABELS = {
    "local": "On this computer (private, works offline)",
    "online": "An online service (OpenAI-compatible)",
    "off": "Off",
}
TEST_SECONDS = 4


class DictationDialog(QDialog):
    def __init__(self, config, parent=None, note=""):
        super().__init__(parent)
        self.config = config
        self.models = dictation.shared("models", config.config_dir / "dictation-models")
        self.models.progress.connect(self._progress)
        self.models.finished.connect(self._downloaded)
        self.setWindowTitle("Dictation")
        chosen = dictation.settings(config)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Press Ctrl+Shift+Space anywhere in Steed and speak; press it again "
            "to finish, or hold it while you talk. The words go where your "
            "cursor is: a field on the page, the address bar or your agent "
            'message. Say "new line" or "new paragraph" for breaks.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.note = QLabel(note)
        self.note.setWordWrap(True)
        self.note.setVisible(bool(note))
        layout.addWidget(self.note)

        layout.addWidget(QLabel("Speech to text"))
        self.engine = QComboBox()
        for key, label in ENGINE_LABELS.items():
            self.engine.addItem(label, key)
        self.engine.setCurrentIndex(list(ENGINE_LABELS).index(chosen["engine"]))
        self.engine.currentIndexChanged.connect(self._shown)
        layout.addWidget(self.engine)

        # On this computer.
        self.model_label = QLabel("Model")
        layout.addWidget(self.model_label)
        self.model = QComboBox()
        for key in dictation.MODELS:
            self.model.addItem("", key)
        self.model.setCurrentIndex(list(dictation.MODELS).index(chosen["model"]))
        self.model.currentIndexChanged.connect(self._shown)
        layout.addWidget(self.model)
        model_buttons = QHBoxLayout()
        self.download = QPushButton("Download model")
        self.download.clicked.connect(self._download)
        self.remove = QPushButton("Remove model")
        self.remove.clicked.connect(self._remove)
        model_buttons.addWidget(self.download)
        model_buttons.addWidget(self.remove)
        layout.addLayout(model_buttons)
        self.model_status = QLabel("")
        self.model_status.setWordWrap(True)
        layout.addWidget(self.model_status)
        self.live = QCheckBox("Show the words while I speak")
        self.live.setChecked(chosen["live"])
        layout.addWidget(self.live)

        # Online.
        self.online_note = QLabel(
            "Audio is sent to this service each time you dictate. Works with "
            "OpenAI, Groq and self-hosted servers such as Speaches or "
            "whisper.cpp. Private windows always dictate on this computer."
        )
        self.online_note.setWordWrap(True)
        layout.addWidget(self.online_note)
        self.url = QLineEdit(chosen["url"])
        self.url.setPlaceholderText(dictation.DEFAULT_URL)
        self.online_model = QLineEdit(chosen["online_model"])
        self.online_model.setPlaceholderText("Model, e.g. whisper-1")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.has_key = bool(dictation.api_key())
        self.key.setPlaceholderText(
            "Saved in your keyring — type to replace"
            if self.has_key
            else "API key (kept in your desktop keyring)"
        )
        for field in (self.url, self.online_model, self.key):
            layout.addWidget(field)
        self.forget = QPushButton("Forget key")
        self.forget.clicked.connect(self._forget)
        layout.addWidget(self.forget)

        self.language = QLineEdit(chosen["language"])
        self.language.setPlaceholderText("Language code, e.g. en or de (blank: detect)")
        layout.addWidget(self.language)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.test = QPushButton("Test microphone")
        self.test.clicked.connect(self._test)
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for button in (self.test, save, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.tester = None
        self._shown()

    def chosen_engine(self):
        return self.engine.currentData()

    def chosen_model(self):
        return self.model.currentData()

    def _shown(self, *_):
        engine = self.chosen_engine()
        local = engine == "local"
        for widget in (
            self.model_label,
            self.model,
            self.download,
            self.remove,
            self.model_status,
            self.live,
        ):
            widget.setVisible(local)
        for widget in (
            self.online_note,
            self.url,
            self.online_model,
            self.key,
            self.forget,
        ):
            widget.setVisible(engine == "online")
        self.forget.setVisible(engine == "online" and self.has_key)
        model = self.chosen_model()
        self.language.setVisible(
            engine == "online"
            or (local and dictation.MODELS[model]["kind"] == "whisper")
        )
        self.test.setVisible(engine != "off")
        for index, key in enumerate(dictation.MODELS):
            entry = dictation.MODELS[key]
            mark = " — downloaded" if self.models.installed(key) else ""
            self.model.setItemText(
                index, f"{entry['label']} · {entry['size']} MB{mark}"
            )
        installed = self.models.installed(model)
        self.download.setVisible(local and not installed)
        self.download.setEnabled(not self.models.downloading(model))
        self.remove.setVisible(local and installed)
        if not dictation.local_available():
            self.model_status.setText(
                "First install the speech engine: run .venv/bin/python -m pip "
                "install -r requirements-dictation.txt in Steed's folder, then "
                "start Steed again."
            )
        elif self.models.downloading(model):
            pass
        elif installed:
            self.model_status.setText("Ready. Everything stays on this computer.")
        else:
            self.model_status.setText(
                f"Downloaded once ({dictation.MODELS[model]['size']} MB) from the "
                "sherpa-onnx project on GitHub."
            )

    # ----- Models -----

    def _download(self):
        model = self.chosen_model()
        self.models.download(model)
        self.model_status.setText("Downloading…")
        self._shown()

    def _progress(self, model, percent):
        if model == self.chosen_model():
            self.model_status.setText(
                f"Downloading… {percent}%" if percent < 100 else "Unpacking…"
            )

    def _downloaded(self, model, error):
        self._shown()
        if model == self.chosen_model() and error:
            self.model_status.setText(error)

    def _remove(self):
        dictation.shared("local").stop()
        self.models.remove(self.chosen_model())
        self._shown()

    # ----- Saving -----

    def save(self):
        engine = self.chosen_engine()
        values = {
            "dictation_engine": engine,
            "dictation_model": self.chosen_model(),
            "dictation_live": self.live.isChecked(),
            "dictation_language": self.language.text().strip()[:8],
        }
        if engine == "online":
            try:
                values["dictation_url"] = dictation.check_url(self.url.text().strip())
            except ValueError as error:
                self.status.setText(str(error))
                return False
            values["dictation_online_model"] = (
                self.online_model.text().strip()[:100] or dictation.DEFAULT_ONLINE_MODEL
            )
            if self.key.text():
                try:
                    dictation.set_api_key(self.key.text())
                except Exception:
                    self.status.setText(
                        "The key could not be saved: no desktop keyring is unlocked."
                    )
                    return False
                self.key.clear()
                self.has_key = True
                self.key.setPlaceholderText("Saved in your keyring — type to replace")
        for name, value in values.items():
            self.config.set(name, value)
        self.status.setText("Saved.")
        self._shown()
        return True

    def _forget(self):
        try:
            dictation.set_api_key("")
        except Exception:
            self.status.setText("The keyring could not be reached.")
            return
        self.has_key = False
        self.key.setPlaceholderText("API key (kept in your desktop keyring)")
        self.status.setText("The key was removed from your keyring.")
        self._shown()

    # ----- Trying it -----

    def _test(self):
        if not self.save():
            return
        window = self.parent()
        controller = getattr(window, "dictation", None)
        if controller is None or controller.busy:
            return
        if not controller.start():
            self.status.setText("Dictation could not start — see the note above.")
            return
        controller.target = ("test", self)
        self.status.setText(f"Speak now… ({TEST_SECONDS} seconds)")
        QTimer.singleShot(TEST_SECONDS * 1000, controller.finish)

    def heard(self, words, error=""):
        if error:
            self.status.setText(f"The test failed: {error}")
        else:
            self.status.setText(
                f"Heard: “{words}”"
                if words
                else "Nothing was heard — is the "
                "right microphone chosen in your system's sound settings?"
            )
