"""Hand a second launch to the browser that already owns the data directory."""

import hashlib
import json
from pathlib import Path

from PyQt6.QtCore import QObject, QStandardPaths, QUrl, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

MAX_MESSAGE = 64 * 1024
MAX_URLS = 20


def server_name(config_dir) -> str:
    """One private socket per data directory, in the user's runtime directory."""
    digest = hashlib.sha256(str(Path(config_dir).resolve()).encode()).hexdigest()
    runtime = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.RuntimeLocation
    )
    name = f"steed-{digest[:16]}"
    return str(Path(runtime) / name) if runtime else name


def launch_urls(arguments) -> list[str]:
    """Keep only web addresses; other arguments are never forwarded."""
    urls = []
    for argument in arguments:
        url = QUrl(argument)
        if url.scheme() in ("http", "https") and url.host() and len(urls) < MAX_URLS:
            urls.append(url.toString())
    return urls


def request_existing(config_dir, urls, timeout_ms=2000) -> bool:
    """Ask the running browser to open a window or these URLs."""
    socket = QLocalSocket()
    socket.connectToServer(server_name(config_dir))
    if not socket.waitForConnected(timeout_ms):
        return False
    payload = json.dumps({"version": 1, "urls": list(urls)}).encode() + b"\n"
    socket.write(payload)
    if not socket.waitForBytesWritten(timeout_ms):
        return False
    # The reply confirms the request was accepted, not just buffered.
    accepted = socket.waitForReadyRead(timeout_ms) and bytes(
        socket.readAll()
    ).startswith(b"ok")
    socket.disconnectFromServer()
    return accepted


class InstanceServer(QObject):
    """Receive launch requests; only the data-directory lock holder listens."""

    open_requested = pyqtSignal(list)

    def __init__(self, config_dir, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        name = server_name(config_dir)
        # Holding the browser lock proves any existing socket file is stale.
        QLocalServer.removeServer(name)
        self.listening = self.server.listen(name)
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            buffer = bytearray()

            def read(socket=socket, buffer=buffer):
                buffer.extend(bytes(socket.readAll()))
                if len(buffer) > MAX_MESSAGE:
                    socket.abort()
                    return
                if b"\n" not in buffer:
                    return
                try:
                    message = json.loads(bytes(buffer).partition(b"\n")[0])
                    if message.get("version") != 1 or not isinstance(
                        message.get("urls"), list
                    ):
                        raise ValueError
                    urls = launch_urls(u for u in message["urls"] if isinstance(u, str))
                except (ValueError, AttributeError):
                    socket.abort()
                    return
                socket.write(b"ok\n")
                socket.flush()
                socket.disconnectFromServer()
                self.open_requested.emit(urls)

            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)

    def close(self):
        self.server.close()
