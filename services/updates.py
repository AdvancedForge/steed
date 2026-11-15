"""Is a newer Steed or a newer web engine available?

Once a day (Settings → General → "Check for updates"), in the background:
Steed asks its git remote whether the branch it follows has new changes
(`git fetch`, nothing is changed in your checkout), and PyPI whether a newer
Qt WebEngine (with newer Chromium security fixes) is out. The result shows on
steed:features and once in the status bar; updating stays your choice
(`./install.sh --update`, and `upgrade_engine.py try` for the engine).
"""

import json
import re
import shutil
import time
from importlib import metadata
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

PROJECT = Path(__file__).resolve().parent.parent
ENGINE_PACKAGE = "PyQt6-WebEngine-Qt6"
PYPI = "https://pypi.org/pypi/{name}/json"
DAY = 24 * 3600
FIRST_CHECK_MS = 60000


def version_key(text):
    return tuple(int(n) for n in re.findall(r"\d+", text or ""))


def newest_stable(releases: dict) -> str | None:
    stable = [
        version
        for version, files in releases.items()
        if files and re.fullmatch(r"\d+(\.\d+)*", version)
    ]
    return max(stable, key=version_key) if stable else None


class Updates(QObject):
    changed = pyqtSignal()

    def __init__(self, config, parent=None, project: Path = PROJECT):
        super().__init__(parent)
        self.config = config
        self.project = project
        self.state = {
            "steed": None,  # changes waiting upstream (int), None: unknown
            "engine": None,  # newer engine version, "" when current
            "installed_engine": self._installed_engine(),
            "checked": 0.0,
            "error": "",
        }
        self.network = None
        self.process = None
        self.timer = QTimer(self)
        self.timer.setInterval(3600 * 1000)
        self.timer.timeout.connect(self.check_if_due)
        self.timer.start()
        QTimer.singleShot(FIRST_CHECK_MS, self.check_if_due)

    @staticmethod
    def _installed_engine():
        try:
            return metadata.version(ENGINE_PACKAGE)
        except metadata.PackageNotFoundError:
            return ""

    @property
    def enabled(self):
        return bool(self.config.get("check_updates", True))

    def check_if_due(self):
        last = float(self.config.get("updates_checked", 0) or 0)
        if self.enabled and time.time() - last > DAY:
            self.check()

    def check(self):
        self.config.set("updates_checked", time.time())
        self.state["checked"] = time.time()
        self.state["error"] = ""
        self._check_engine()
        self._check_steed()

    # -- the engine, from PyPI

    def _check_engine(self):
        if self.network is None:
            self.network = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl(PYPI.format(name=ENGINE_PACKAGE)))
        request.setTransferTimeout(30000)
        reply = self.network.get(request)
        reply.finished.connect(lambda: self._engine_answer(reply))

    def _engine_answer(self, reply):
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.state["error"] = "PyPI could not be reached."
            self.changed.emit()
            return
        try:
            latest = newest_stable(json.loads(bytes(reply.readAll()))["releases"])
        except (ValueError, KeyError, TypeError):
            latest = None
        current = self.state["installed_engine"]
        self.state["engine"] = (
            latest if latest and version_key(latest) > version_key(current) else ""
        )
        self.changed.emit()

    # -- Steed, from its git remote

    def _check_steed(self):
        git = shutil.which("git")
        if git is None or not (self.project / ".git").exists():
            return
        process = self.process = QProcess(self)
        process.setWorkingDirectory(str(self.project))
        process.setProgram(git)
        process.setArguments(["fetch", "--quiet"])
        process.finished.connect(lambda code, _status: self._fetched(git, code))
        process.errorOccurred.connect(lambda _error: self._fetched(git, 1))
        process.start()

    def _fetched(self, git, code):
        if self.process is None:
            return
        self.process.deleteLater()
        self.process = None
        if code != 0:
            return
        count = QProcess(self)
        count.setWorkingDirectory(str(self.project))
        count.start(git, ["rev-list", "--count", "HEAD..@{upstream}"])
        if count.waitForFinished(5000) and count.exitCode() == 0:
            text = bytes(count.readAllStandardOutput()).decode().strip()
            self.state["steed"] = int(text) if text.isdigit() else None
            self.changed.emit()
        count.deleteLater()

    def summary(self):
        """What to tell, in words (empty when nothing is new)."""
        parts = []
        if self.state["steed"]:
            n = self.state["steed"]
            parts.append(
                f"A newer Steed is available ({n} change{'s' * (n != 1)}): "
                "run ./install.sh --update."
            )
        if self.state["engine"]:
            parts.append(
                f"Qt WebEngine {self.state['engine']} is available, with newer "
                "Chromium security fixes: run .venv/bin/python upgrade_engine.py try."
            )
        return " ".join(parts)

    def close(self):
        self.timer.stop()
        if self.process is not None:
            self.process.kill()
            self.process.waitForFinished(1000)
            self.process = None
