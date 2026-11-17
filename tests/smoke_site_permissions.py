"""Real Chromium notification requests exercise once/saved/private/forget choices."""

import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt, QUrl
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qt_test_support import application, keep_directory  # noqa: E402

from browser.browser_window import BrowserWindow, QWebEnginePermission  # noqa: E402
from browser.config import Config  # noqa: E402


def check():
    if QWebEnginePermission is None:
        raise RuntimeError("This smoke check requires Qt 6.8 or newer")
    app = application()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"""<!doctype html><button style='position:absolute;left:10px;top:10px;width:200px;height:60px'
            onclick='Notification.requestPermission().then(value => window.result = value)'>Request notifications</button>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def wait(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("Website permission smoke timed out")

    def javascript(page, source):
        result = []
        page.runJavaScript(source, result.append)
        wait(lambda: bool(result))
        return result[0]

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    directory = tempfile.TemporaryDirectory()
    keep_directory(directory)
    window = None
    try:
        with patch.object(Path, "home", return_value=Path(directory.name)):
            config = Config()
            config.update(
                {"home_page": "about:blank", "restore_tabs_on_startup": False}
            )
            window = BrowserWindow()
            window.show()
            origin = f"http://127.0.0.1:{server.server_port}"
            decisions = ["once", "profile", "deny", "global", "deny"]
            shown = []

            def decide(dialog):
                choice = decisions.pop(0)
                shown.append(choice)
                if choice == "deny":
                    return QDialog.DialogCode.Rejected
                index = dialog.remember.findData(choice)
                assert index >= 0
                dialog.remember.setCurrentIndex(index)
                return QDialog.DialogCode.Accepted

            def request():
                view = window.tabs[window.current_tab_index]
                loaded = []
                view.loadFinished.connect(loaded.append)
                view.setUrl(QUrl("about:blank"))
                wait(lambda: bool(loaded))
                loaded.clear()
                view.setUrl(QUrl(origin))
                wait(lambda: bool(loaded))
                view.loadFinished.disconnect(loaded.append)
                assert loaded[-1]
                page = view.page()
                assert javascript(page, "typeof Notification") == "function"
                QTest.mouseClick(
                    view.focusProxy() or view,
                    Qt.MouseButton.LeftButton,
                    pos=QPoint(100, 35),
                )
                wait(lambda: javascript(page, "window.result || ''") != "")
                return javascript(page, "window.result")

            store = window.profile_manager.registry.site_permissions
            with patch("ui.site_permissions.SitePermissionDialog.exec", decide):
                assert request() == "granted"
                assert shown == ["once"] and not store.rules()
                assert request() == "granted"
                assert shown == ["once", "profile"] and len(store.rules()) == 1
                assert request() == "granted"
                assert shown == ["once", "profile"]
                store.forget(store.rules()[0]["id"])
                assert request() == "denied"
                assert shown[-1] == "deny" and not store.rules()
                assert request() == "granted"
                assert store.rules()[0]["mode"] == "global"
                window._switch_to_profile("search")
                assert request() == "denied"
                assert shown == ["once", "profile", "deny", "global", "deny"]
                assert not store.private_rules and len(store.rules()) == 1
                assert not decisions
            print(
                "Native notification once, saved reuse, forget and private isolation passed."
            )
    finally:
        if window is not None:
            window.close()
            sip.delete(window)
        server.shutdown()
        server.server_close()
        worker.join(2)


if __name__ == "__main__":
    check()
