"""Ask the person's agent one question, from its command line.

For small judgments that need no browser access and no conversation: does
this page change match what was asked (watches), how should these tabs be
grouped (tab organizer). Any assistant works: a model you added (asked over its
chat API, with no tools), else Claude Code (`claude -p`) or Codex (`codex
exec`), whichever is installed. The "watch_agent" setting picks one ("claude",
"codex" or "model:<name>"). Only the prompt is sent; the reply is plain text.
"""

import json
import shutil

from PyQt6.QtCore import QByteArray, QObject, QProcess, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

TIMEOUT_MS = 120000
NAMES = {"claude": "Claude Code", "codex": "Codex"}


def agent_commands():
    return {
        "claude": ["claude", "-p", "--no-session-persistence"],
        "codex": ["codex", "exec", "--skip-git-repo-check", "-"],
    }


def _models(config):
    from services.model_assistant import clean_model

    found = []
    for entry in (config.get("models", []) if config is not None else []) or []:
        try:
            found.append(clean_model(entry))
        except ValueError:
            continue
    return found


def agent_command(config):
    """What to ask: a model you added (its settings, a dict), else the command
    line of the first installed coding agent; None when there is neither."""
    choice = config.get("watch_agent", "") if config is not None else ""
    models = _models(config)
    if isinstance(choice, str) and choice.startswith("model:"):
        chosen = [m for m in models if "model:" + m["name"] == choice]
        if chosen:
            return chosen[0]
    commands = agent_commands()
    if choice in commands and shutil.which(commands[choice][0]):
        return commands[choice]
    if models:
        return models[0]
    for name in commands:
        if shutil.which(commands[name][0]):
            return commands[name]
    return None


def helper_choices(config):
    """The assistants that can help: [(key, label)] for the "watch_agent"
    setting, models you added first, then coding agents installed here."""
    found = [("model:" + m["name"], m["name"]) for m in _models(config)]
    found += [(key, NAMES[key]) for key in installed_agents()]
    return found


def installed_agents():
    """The coding agents on this computer that can answer one question."""
    return [k for k, c in agent_commands().items() if shutil.which(c[0])]


def helper_chosen(config):
    """The helper you chose, if it is still there; else None."""
    choice = config.get("watch_agent", "") if config is not None else ""
    return choice if choice in dict(helper_choices(config)) else None


def agent_name(command):
    if isinstance(command, dict):
        return command["name"]
    executable = (command or [""])[0]
    return next(
        (label for key, label in NAMES.items() if executable.endswith(key)),
        "your agent",
    )


class AgentOnce(QObject):
    """One question in flight: answered(text) or failed(reason)."""

    answered = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, command, prompt, parent=None, timeout_ms=TIMEOUT_MS):
        super().__init__(parent)
        self.name = agent_name(command)
        self.title = self.name[:1].upper() + self.name[1:]  # Starts a sentence.
        self.output = bytearray()
        self.done = False
        self.process = None
        self.reply = None
        self.timer = QTimer(self, singleShot=True, interval=timeout_ms)
        self.timer.timeout.connect(self._timeout)
        if isinstance(command, dict):
            self._ask_model(command, prompt)
            return
        self.process = QProcess(self)
        self.process.setProgram(command[0])
        self.process.setArguments(command[1:])
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.process.start()
        self.process.write(prompt.encode())
        self.process.closeWriteChannel()
        self.timer.start()

    def _ask_model(self, entry, prompt):
        from services.model_assistant import api_key

        try:
            key = api_key(entry)
        except ValueError as error:
            reason = str(error)
            QTimer.singleShot(0, lambda: self._fail(reason))
            return
        request = QNetworkRequest(QUrl(entry["base_url"] + "/chat/completions"))
        request.setHeader(
            QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json"
        )
        if key:
            request.setRawHeader(b"Authorization", f"Bearer {key}".encode())
        # The key goes to that address only: no redirects, no cookies.
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        for attribute in (
            QNetworkRequest.Attribute.CookieLoadControlAttribute,
            QNetworkRequest.Attribute.CookieSaveControlAttribute,
            QNetworkRequest.Attribute.AuthenticationReuseAttribute,
        ):
            request.setAttribute(attribute, QNetworkRequest.LoadControl.Manual)
        body = {
            "model": entry["model"],
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        self.network = QNetworkAccessManager(self)
        reply = self.reply = self.network.post(
            request, QByteArray(json.dumps(body).encode())
        )

        def finish():
            reply.deleteLater()
            if self.done:
                return
            data = bytes(reply.readAll()[: 4 * 1024 * 1024])
            try:
                if reply.error() != QNetworkReply.NetworkError.NoError:
                    raise ValueError
                text = json.loads(data)["choices"][0]["message"]["content"]
                if not isinstance(text, str):
                    raise ValueError
            except (ValueError, KeyError, TypeError, IndexError):
                status = reply.attribute(
                    QNetworkRequest.Attribute.HttpStatusCodeAttribute
                )
                why = f" (HTTP {status})" if status else ""
                self._fail(
                    f"{entry['name']} did not answer{why}. Check its address, "
                    "model and key in Agents → Add an Assistant…."
                )
                return
            self.done = True
            self.timer.stop()
            self.answered.emit(text)

        reply.finished.connect(finish)
        self.timer.start()

    def _fail(self, reason):
        if not self.done:
            self.done = True
            self.timer.stop()
            self.failed.emit(reason)

    @property
    def running(self):
        return not self.done

    def _read(self):
        self.output.extend(bytes(self.process.readAllStandardOutput()))

    def _finished(self, code, _status):
        if self.done:
            return
        self._read()
        self.done = True
        self.timer.stop()
        if code == 0:
            self.answered.emit(self.output.decode("utf-8", "replace"))
        else:
            self.failed.emit(self._why_not(code))

    def _why_not(self, code):
        """Which assistant, what it said last, and what to try."""
        said = bytes(self.process.readAllStandardError()).decode("utf-8", "replace")
        lines = [line.strip() for line in said.splitlines() if line.strip()]
        detail = f": {lines[-1][:160]}" if lines else f" (it stopped with code {code})"
        return (
            f"{self.title} did not answer{detail}. Check that {self.name} works and "
            "is signed in, or choose another assistant in Agents → Add an "
            "Assistant…."
        )

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart and not self.done:
            self.done = True
            self.timer.stop()
            self.failed.emit(
                f"{self.title} could not be started. Choose another assistant in "
                "Agents → Add an Assistant…."
            )

    def _timeout(self):
        if self.done:
            return
        if self.process is not None:
            self.process.kill()
        else:
            self._fail(f"{self.title} did not answer in time.")
            if self.reply is not None:
                self.reply.abort()

    def stop(self):
        self.done = True
        self.timer.stop()
        if self.reply is not None and not self.reply.isFinished():
            self.reply.abort()
        if self.process is None:
            return
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
