#!/usr/bin/env python3
"""Entry point for the custom browser."""
import os
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QCoreApplication
from browser_window import BrowserWindow


def main():
    """Main entry point."""
    # Helps QtWebEngine video pipelines on many systems
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    # Reduce autoplay pain (Discord/Slack often rely on it)
    extra_flags = [
        "--ignore-gpu-blocklist",
        "--enable-gpu-rasterization",
        "--use-gl=desktop",
        "--enable-features=WebRTCPipeWireCapturer",
        "--enable-web-security"
    ]
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    for f in extra_flags:
        if f not in flags:
            flags = (flags + " " + f).strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags


    # Enable high DPI scaling for Framework 12's high-resolution display
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    
    # Note: AA_UseOpenGLES removed as it may not be supported in PyQt6
    # Hardware acceleration is enabled by default in Qt WebEngine
    
    app = QApplication(sys.argv)
    app.setApplicationName("Custom Browser")
    app.setOrganizationName("CustomBrowser")
    
    window = BrowserWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
