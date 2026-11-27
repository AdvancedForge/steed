#!/usr/bin/env python3
"""Entry point for the custom browser."""

import logging
import os
import shlex
import sys

from PyQt6.QtCore import QCoreApplication, QLockFile, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from browser.browser_window import BrowserWindow
from browser.config import Config
from browser.single_instance import InstanceServer, launch_urls, request_existing
from browser.webengine import prepare_engine_environment


def configure_chromium_flags(thin_scrollbars: bool = True) -> None:
    """Enable PipeWire capture without forcing Qt's graphics backend, and
    (unless turned off) thin overlay scrollbars on websites that appear
    while scrolling, like the ones on Steed's own pages."""
    flags = shlex.split(os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""))
    feature_prefix = "--enable-features="
    wanted = ["WebRTCPipeWireCapturer"]
    if thin_scrollbars:
        wanted.append("FluentOverlayScrollbar")
    for index, flag in enumerate(flags):
        if flag.startswith(feature_prefix):
            features = flag.removeprefix(feature_prefix).split(",")
            features += [name for name in wanted if name not in features]
            flags[index] = feature_prefix + ",".join(features)
            break
    else:
        flags.append(feature_prefix + ",".join(wanted))
    # Protected streaming with Firefox's Widevine module (see widevine.py).
    if not any(flag.startswith("--widevine-path=") for flag in flags):
        from browser import widevine

        flags += widevine.flags()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = shlex.join(flags)


def open_launch_request(urls) -> None:
    """Open a second launch's request in the most recently used window."""
    windows = [
        widget
        for widget in QApplication.topLevelWidgets()
        if isinstance(widget, BrowserWindow)
        and widget.isVisible()
        and not widget._closing
    ]
    if not windows:
        return
    # The window you used last (not a site app's), else any.
    regular = [w for w in windows if not w.app_url] or windows
    owner = windows[0].profile_manager.registry.last_active_owner
    last = owner.parent() if owner is not None else None
    active = QApplication.activeWindow()
    target = active if active in regular else last if last in regular else regular[-1]
    from browser.site_apps import APP_PREFIX

    apps = [u.removeprefix(APP_PREFIX) for u in urls if u.startswith(APP_PREFIX)]
    urls = [u for u in urls if not u.startswith(APP_PREFIX)]
    for app in apps:
        target = target.open_site_app(app)
    if urls:
        for url in urls:
            target._new_tab(url)
    elif not apps:
        # Opening Steed while it runs (Settings → General → "When Steed is
        # opened again"): your window in front, a new window, or a new tab.
        choice = target.config.get("open_again", "front")
        if choice == "window":
            target = target.new_window()
        elif choice == "tab":
            target._new_tab()
        if target.isMinimized():
            target.showNormal()
    target.raise_()
    target.activateWindow()


def take_data_dir(arguments):
    """--data-dir FOLDER (or --data-dir=FOLDER): run on that folder's data
    instead of ~/.custom_browser. Removed from arguments; child processes see
    it as STEED_DATA_DIR."""
    import os

    from browser.config import DATA_ENV

    for index, argument in enumerate(list(arguments[1:]), start=1):
        if argument == "--data-dir" and index + 1 < len(arguments):
            os.environ[DATA_ENV] = arguments[index + 1]
            del arguments[index : index + 2]
            return
        if argument.startswith("--data-dir="):
            os.environ[DATA_ENV] = argument.split("=", 1)[1]
            del arguments[index]
            return


def main() -> int:
    """Create the browser and return the Qt event loop's exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    take_data_dir(sys.argv)
    configure_chromium_flags(Config().get("thin_scrollbars", True) is not False)
    prepare_engine_environment()
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    # Enable high DPI scaling for Framework 12's high-resolution display
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    from ui.lumen import apply_ui_font

    apply_ui_font(app)
    app.setApplicationName("Custom Browser")
    app.setOrganizationName("CustomBrowser")

    # Prevent separate processes from corrupting the same Chromium stores.
    config = Config()
    from browser import error_hook

    error_hook.install(config.config_dir)
    app._profile_lock = QLockFile(str(config.config_dir / "browser.lock"))
    urls = launch_urls(sys.argv[1:])
    if not app._profile_lock.tryLock(0):
        if request_existing(config.config_dir, urls):
            return 0
        QMessageBox.warning(
            None,
            "Browser Already Running",
            "This browser data directory is in use by a browser that is not "
            "responding. Wait for it to finish starting, or close it and try again.",
        )
        return 1
    app._instance_server = InstanceServer(config.config_dir)
    app._instance_server.open_requested.connect(open_launch_request)

    from browser.site_apps import APP_PREFIX

    apps = [u.removeprefix(APP_PREFIX) for u in urls if u.startswith(APP_PREFIX)]
    urls = [u for u in urls if not u.startswith(APP_PREFIX)]
    if apps and not urls:
        # Opened from an app's menu entry: just the app.
        window = BrowserWindow(app_url=apps[0])
        window.show()
        for app in apps[1:]:
            window.open_site_app(app)
    else:
        window = BrowserWindow()
        window.show()
        for url in urls:
            window._new_tab(url)
        for app in apps:
            window.open_site_app(app)

    code = app.exec()
    from services.dictation import stop_shared

    stop_shared()
    # Before the application goes: see qt_cleanup.
    from browser.qt_cleanup import release_leftovers

    release_leftovers(app)
    return code


if __name__ == "__main__":
    sys.exit(main())
