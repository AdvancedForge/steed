"""Whether Steed's own interface uses HTML surfaces or native Qt widgets.

Each HTML surface runs its own renderer process (about 12 MB). The native
mode trades the customizable HTML presentation for plain Qt widgets.
"""

from PyQt6.QtWidgets import QApplication

PROPERTY = "steed_native_interface"


def native() -> bool:
    app = QApplication.instance()
    return bool(app is not None and app.property(PROPERTY))


def set_native(value: bool) -> None:
    app = QApplication.instance()
    if app is not None:
        app.setProperty(PROPERTY, bool(value))
