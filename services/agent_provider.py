"""Shared contract for external runtimes; providers never own browser authority."""

from PyQt6.QtCore import QObject, pyqtSignal


class AgentProvider(QObject):
    request_resolved = pyqtSignal(str)
    requested = pyqtSignal(str, object)
    ready = pyqtSignal()
    accepted = pyqtSignal(str)
    message = pyqtSignal(str, str, bool)
    activity = pyqtSignal(str)
    turn_done = pyqtSignal()
    failed = pyqtSignal(str)

    def start(self, discovery, binding_file):
        raise NotImplementedError

    def send(self, message_id, text):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError
