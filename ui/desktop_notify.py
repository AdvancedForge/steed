"""Desktop notifications (org.freedesktop.Notifications, over D-Bus).

Used when an agent replies or needs an approval while Steed is not in front.
Clicking a notification runs its callback (to bring the conversation up).
Without a notification service it quietly does nothing, and so it does while
STEED_NO_DESKTOP_NOTIFICATIONS is set (the tests set it).
"""

import os

from PyQt6.QtCore import QMetaType, QObject, QVariant, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusMessage

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
INTERFACE = "org.freedesktop.Notifications"
LIMIT = 40  # Callbacks kept for notifications not yet clicked or closed.


class DesktopNotifier(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bus = QDBusConnection.sessionBus()
        self.callbacks = {}
        self.available = self.bus.isConnected() and not os.environ.get(
            "STEED_NO_DESKTOP_NOTIFICATIONS"
        )
        if self.available:
            self.bus.connect(SERVICE, PATH, INTERFACE, "ActionInvoked", self._invoked)
            self.bus.connect(
                SERVICE, PATH, INTERFACE, "NotificationClosed", self._closed
            )

    def notify(self, summary, body="", on_click=None, replaces=0):
        """Show a notification; returns its id (0 when none could be shown)."""
        if not self.available:
            return 0
        message = QDBusMessage.createMethodCall(SERVICE, PATH, INTERFACE, "Notify")
        # D-Bus wants exact types: an unsigned id, a list of strings, an int.
        message.setArguments(
            [
                "Steed",
                _typed(int(replaces), QMetaType.Type.UInt),
                "web-browser",
                summary[:120],
                body[:400],
                _typed(
                    ["default", "Open"] if on_click else [], QMetaType.Type.QStringList
                ),
                {},
                _typed(8000, QMetaType.Type.Int),
            ]
        )
        reply = self.bus.call(message)
        if (
            reply.type() != QDBusMessage.MessageType.ReplyMessage
            or not reply.arguments()
        ):
            return 0
        identity = int(reply.arguments()[0])
        if on_click is not None:
            self.callbacks[identity] = on_click
            while len(self.callbacks) > LIMIT:
                self.callbacks.pop(next(iter(self.callbacks)))
        return identity

    @pyqtSlot("uint", "QString")
    def _invoked(self, identity, action):
        callback = self.callbacks.pop(int(identity), None)
        if callback is not None and action == "default":
            callback()

    @pyqtSlot("uint", "uint")
    def _closed(self, identity, _reason):
        self.callbacks.pop(int(identity), None)


def _typed(value, kind):
    variant = QVariant(value)
    variant.convert(QMetaType(kind.value))
    return variant
