#!/usr/bin/env python3
"""Entry point for the custom browser."""

import logging
import os
import shlex
import sys

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication

from browser_window import BrowserWindow


def configure_chromium_flags() -> None:
    """Enable PipeWire capture without forcing Qt's graphics backend."""
    flags = shlex.split(os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""))
    feature_prefix = "--enable-features="
    for index, flag in enumerate(flags):
        if flag.startswith(feature_prefix):
            features = flag.removeprefix(feature_prefix).split(",")
            if "WebRTCPipeWireCapturer" not in features:
                features.append("WebRTCPipeWireCapturer")
            flags[index] = feature_prefix + ",".join(features)
            break
    else:
        flags.append(feature_prefix + "WebRTCPipeWireCapturer")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = shlex.join(flags)


def main() -> int:
    """Create the browser and return the Qt event loop's exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    configure_chromium_flags()
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    # Enable high DPI scaling for Framework 12's high-resolution display
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Custom Browser")
    app.setOrganizationName("CustomBrowser")

    window = BrowserWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
