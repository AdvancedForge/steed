"""One QApplication per test process; join engine cleanup before removing data."""

import atexit
import os
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication

from browser.webengine import prepare_engine_environment

_application = None
_directories = []

# How long tests wait for asynchronous engine work. Waits return as soon as
# their condition holds, so this only bounds failures; it is generous because
# a heavily loaded machine can take many seconds to start renderers and
# helper processes. STEED_TEST_WAIT overrides it (seconds).
WAIT_SECONDS = float(os.environ.get("STEED_TEST_WAIT", "30"))


# One-time tips stay out of the way of tests (test_tips turns them on).
os.environ.setdefault("STEED_NO_TIPS", "1")


def application():
    global _application
    if _application is None:
        prepare_engine_environment()
        # Tests never fetch real New Tab photos: a closed local port fails at
        # once (tests of the photos point these at their own server).
        import services.newtab_photo as photos

        photos.LIST_URL = "http://127.0.0.1:9/list?page={page}"
        photos.IMAGE_URL = "http://127.0.0.1:9/id/{id}.jpg"
        photos.COMMONS_URL = "http://127.0.0.1:9/commons?{letter}"
        # Nor dangerous-site lists; and a program download is kept without
        # asking (test_safety asks).
        import services.safe_browsing as safety
        from browser import download_manager

        safety.BASE = "http://127.0.0.1:9/"
        safety.LISTS = {kind: safety.BASE + kind for kind in safety.LISTS}
        download_manager.confirm_program = lambda name, parent: True
        # Nor ask GitHub or PyPI about updates.
        import services.updates as updates

        updates.PYPI = "http://127.0.0.1:9/{name}"
        # Which coding agents are installed here never decides a test (and
        # tests never start the person's own Claude Code or Codex).
        import services.agent_once as agent_once

        agent_once.installed_agents = lambda: []
        updates.PROJECT = Path("/nonexistent-steed-checkout")
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        _application = QApplication.instance() or QApplication(["steed-tests"])
        from ui.lumen import apply_ui_font

        apply_ui_font(_application)
        _application.setQuitOnLastWindowClosed(False)
    return _application


def keep_directory(directory):
    """Engine storage may still be flushing after pages/profiles are destroyed."""
    _directories.append(directory)


@atexit.register
def shutdown():
    global _application
    if _application is not None and not sip.isdeleted(_application):
        from browser.qt_cleanup import release_leftovers

        release_leftovers(_application)
        sip.delete(_application)
    _application = None
    for directory in _directories:
        directory.cleanup()
    _directories.clear()
