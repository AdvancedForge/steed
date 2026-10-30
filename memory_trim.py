"""Hand freed heap memory back to the operating system after big closes.

glibc keeps memory freed by closed tabs and windows in its arenas, so the
process stays large although nothing leaked (measured: ~100 MB after closing
15 tabs). malloc_trim returns it. Calls are debounced onto the Qt thread.
"""

import ctypes
import ctypes.util
import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

DELAY_MS = 5000
_timer = None
_trim = None


def _load():
    global _trim
    if _trim is None:
        try:
            _trim = ctypes.CDLL(
                ctypes.util.find_library("c") or "libc.so.6"
            ).malloc_trim
            _trim.argtypes = [ctypes.c_size_t]
        except (OSError, AttributeError):
            _trim = False  # Not glibc; nothing to do.
    return _trim


def release_now():
    trim = _load()
    if trim:
        trim(0)


def schedule():
    """Trim once, DELAY_MS after the last close in a burst."""
    global _timer
    app = QApplication.instance()
    if app is None or not _load():
        return
    if _timer is None:
        _timer = QTimer(app)
        _timer.setSingleShot(True)
        _timer.setInterval(DELAY_MS)
        _timer.timeout.connect(release_now)
    _timer.start()
