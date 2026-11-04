"""steed:userscripts: your own CSS and JavaScript for the sites you choose."""

from secrets import token_hex

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ui.html_surface import HtmlSurface
from ui.prompts import MessageBox

STARTER_CSS = """/* Applied as each page starts loading. */
body { line-height: 1.6 !important; }
"""
STARTER_JS = """// Runs in the page, like a userscript. `document` is the page's document.
console.log("Hello from Steed on", location.hostname);
"""


class UserScriptsPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.registry = window.profile_manager.registry
        self.context = token_hex(16)
        self.sequence = 0
        self.notice = self.error = ""
        self.disposed = False
        self.store = None
        self.surface = HtmlSurface("userscripts", self, label="userscripts")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.surface)
        self.surface.command.connect(self.command)
        window.profile_manager.profile_changed.connect(self._profile_changed)
        self._profile_changed()

    @property
    def profile(self):
        return self.window.profile_manager.current_profile_name

    def _profile_changed(self, *_):
        if self.store is not None:
            self.store.changed.disconnect(self.publish)
        self.store = self.registry.user_scripts(self.profile)
        if self.store is not None:
            self.store.changed.connect(self.publish)
        self.publish()

    def publish(self):
        if self.disposed:
            return
        self.surface.publish(
            {
                "context": self.context,
                "ack": self.sequence,
                "private": self.store is None,
                "profile": self.profile,
                "entries": [] if self.store is None else self.store.entries,
                "starter": {"css": STARTER_CSS, "js": STARTER_JS},
                "dark": self.registry.page_style.dark(self.profile),
                "notice": self.notice,
                "error": self.error,
            },
            force=True,
        )

    def command(self, command):
        if self.disposed or command.get("context") != self.context:
            return
        sequence = command.get("sequence")
        if type(sequence) is not int or sequence <= self.sequence:
            return
        self.sequence = sequence
        self.notice = self.error = ""
        action = command.get("action")
        try:
            if self.store is None:
                raise ValueError("User scripts are not available in incognito windows.")
            if action == "save":
                self.store.save(
                    {
                        key: command.get(key)
                        for key in (
                            "id",
                            "name",
                            "sites",
                            "css",
                            "js",
                            "when",
                            "enabled",
                        )
                    }
                )
                self.notice = "Saved. It applies to pages you load or reload from now."
            elif action == "toggle":
                self.store.set_enabled(
                    command.get("id"), command.get("enabled") is True
                )
                self.notice = "Saved. Reload open pages to see the change."
            elif action == "delete":
                entry = self.store.get(command.get("id"))
                if entry is None:
                    raise ValueError("That entry no longer exists.")
                reply = MessageBox.question(
                    self.window, "Delete", f"Delete “{entry['name']}”?"
                )
                if reply == MessageBox.StandardButton.Yes:
                    self.store.remove(entry["id"])
                    self.notice = "Deleted."
            else:
                raise ValueError("Unsupported request.")
        except ValueError as error:
            self.error = str(error)
        self.publish()

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        if self.store is not None:
            self.store.changed.disconnect(self.publish)
        self.window.profile_manager.profile_changed.disconnect(self._profile_changed)
        self.surface.dispose()
