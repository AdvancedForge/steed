"""Launch the real entry point, render a local page, and close automatically."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtCore import QTimer, qVersion
from PyQt6.QtWidgets import QApplication

# This script can be run directly from the project directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from browser.browser_window import BrowserWindow  # noqa: E402
from browser.config import Config  # noqa: E402


def smoke_test() -> None:
    failures = []
    rendered = []
    applications = []

    def create_application(arguments):
        app = QApplication(arguments)
        applications.append(app)

        def finish(error=None):
            if error is not None:
                failures.append(error)
            for widget in app.topLevelWidgets():
                if isinstance(widget, BrowserWindow) and not widget._closing:
                    widget.close()
            app.quit()

        def inspect_window():
            window = next(
                widget
                for widget in app.topLevelWidgets()
                if isinstance(widget, BrowserWindow)
            )

            def check_output(output):
                if not isinstance(output, str) or "canPlayType:" not in output:
                    finish(
                        "The local diagnostic page did not render its JavaScript output"
                    )
                    return
                rendered.append(output)
                finish()

            def page_loaded(ok):
                if not ok:
                    finish("The local diagnostic page failed to load")
                    return
                window.tabs[0].page().runJavaScript(
                    "document.getElementById('out').textContent", check_output
                )

            window.tabs[0].loadFinished.connect(page_loaded)

        QTimer.singleShot(0, inspect_window)
        QTimer.singleShot(10000, lambda: finish("Startup smoke check timed out"))
        return app

    with tempfile.TemporaryDirectory() as directory:
        with patch("pathlib.Path.home", return_value=Path(directory)):
            config = Config()
            config.set(
                "home_page",
                (
                    Path(__file__).resolve().parent / "fixtures" / "codec_test.html"
                ).as_uri(),
            )
            with patch.object(
                main, "QApplication", side_effect=create_application
            ) as factory:
                factory.setHighDpiScaleFactorRoundingPolicy = (
                    QApplication.setHighDpiScaleFactorRoundingPolicy
                )
                result = main.main()
            for app in applications:
                sip.delete(app)
    if result != 0 or failures or not rendered:
        raise RuntimeError(
            f"Startup smoke check failed: exit={result}, errors={failures}"
        )
    print(f"Startup, local rendering, and shutdown passed with Qt {qVersion()}")


if __name__ == "__main__":
    smoke_test()
