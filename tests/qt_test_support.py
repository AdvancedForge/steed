"""One QApplication per test process; join engine cleanup before removing data."""

import atexit

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication

_application = None
_directories = []


def application():
    global _application
    if _application is None:
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
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        sip.delete(_application)
    _application = None
    for directory in _directories:
        directory.cleanup()
    _directories.clear()
