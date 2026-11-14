"""Ask the person's agent one question, from its command line.

For small judgments that need no browser access and no conversation: does
this page change match what was asked (watches), how should these tabs be
grouped (tab organizer). Claude Code (`claude -p`) or Codex (`codex exec`),
whichever is installed (the "watch_agent" setting picks one). The prompt goes
on stdin; the reply is plain text. Nothing else is shared.
"""

import shutil

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

TIMEOUT_MS = 120000
NAMES = {"claude": "Claude Code", "codex": "Codex"}


def agent_commands():
    return {
        "claude": ["claude", "-p", "--no-session-persistence"],
        "codex": ["codex", "exec", "--skip-git-repo-check", "-"],
    }


def agent_command(config):
    """The command line to ask (first installed), or None."""
    choice = config.get("watch_agent", "") if config is not None else ""
    commands = agent_commands()
    order = [choice] if choice in commands else list(commands)
    for name in order:
        if name in commands and shutil.which(commands[name][0]):
            return commands[name]
    return None


def agent_name(command):
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
        self.output = bytearray()
        self.done = False
        self.process = QProcess(self)
        self.process.setProgram(command[0])
        self.process.setArguments(command[1:])
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.timer = QTimer(self, singleShot=True, interval=timeout_ms)
        self.timer.timeout.connect(self._timeout)
        self.process.start()
        self.process.write(prompt.encode())
        self.process.closeWriteChannel()
        self.timer.start()

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
            self.failed.emit("The agent did not answer.")

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart and not self.done:
            self.done = True
            self.timer.stop()
            self.failed.emit("The agent could not be started.")

    def _timeout(self):
        if not self.done:
            self.process.kill()

    def stop(self):
        self.done = True
        self.timer.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
