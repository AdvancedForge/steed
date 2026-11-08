"""One QApplication per test process; join engine cleanup before removing data."""

import atexit
import os

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication

from webengine import prepare_engine_environment

_application = None
_directories = []

# How long tests wait for asynchronous engine work. Waits return as soon as
# their condition holds, so this only bounds failures; it is generous because
# a heavily loaded machine can take many seconds to start renderers and
# helper processes. STEED_TEST_WAIT overrides it (seconds).
WAIT_SECONDS = float(os.environ.get("STEED_TEST_WAIT", "30"))


def application():
    global _application
    if _application is None:
        prepare_engine_environment()
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        _application = QApplication.instance() or QApplication(["steed-tests"])
        _application.setQuitOnLastWindowClosed(False)
    return _application


def keep_directory(directory):
    """Engine storage may still be flushing after pages/profiles are destroyed."""
    _directories.append(directory)


@atexit.register
def shutdown():
    global _application
    if _application is not None and not sip.isdeleted(_application):
        from qt_cleanup import release_leftovers

        release_leftovers(_application)
        sip.delete(_application)
    _application = None
    for directory in _directories:
        directory.cleanup()
    _directories.clear()
