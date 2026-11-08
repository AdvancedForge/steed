"""Destroy Steed's leftover Qt objects before the application object goes.

When the QApplication is deleted (at quit, or when Python finalizes), PyQt
walks its table of every wrapped object and destroys the parentless ones that
Python owns. Destroying a window or a service runs Python code (signals,
reimplemented methods) that wraps new objects; when that grows the table in the
middle of the walk, PyQt reads freed memory and Steed crashes on its way out,
after everything was saved. Destroying them here first, while nothing walks
the table, leaves PyQt nothing to do.
"""

import gc

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject


def _leftovers(app):
    found = []
    for item in gc.get_objects():
        try:
            if (
                isinstance(item, QObject)
                and item is not app
                and not sip.isdeleted(item)
                and sip.ispyowned(item)
                and item.parent() is None
            ):
                found.append(item)
        except RuntimeError:
            continue  # Its C++ object went while looking.
    return found


def release_leftovers(app=None):
    """Delete parentless Qt objects Python owns: windows first, then the rest."""
    app = app or QCoreApplication.instance()
    if app is None:
        return
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for _ in range(3):  # Deleting one can leave others parentless.
        found = _leftovers(app)
        if not found:
            break
        found.sort(key=lambda item: not item.isWidgetType())
        for item in found:
            if not sip.isdeleted(item):
                sip.delete(item)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
