"""steed:apps: create and edit the HTML apps shown in the App Panel."""

from pathlib import Path
from secrets import token_hex

from PyQt6.QtWidgets import QFileDialog, QVBoxLayout, QWidget

from ui.html_surface import HtmlSurface
from ui.prompts import MessageBox

STARTER = """<!-- A panel app: HTML, CSS and JavaScript in one page. -->
<style>
  body { font: 14px system-ui; margin: 14px; }
  textarea { width: 100%; height: 140px; }
  li { margin: 4px 0; }
</style>
<h2 id="clock"></h2>
<textarea id="notes" placeholder="Notes are kept in this app's own storage"></textarea>
<h3>Open tabs</h3>
<ul id="tabs"><li>Allow "See open tabs" for local pages in steed:access.</li></ul>
<script>
  const tick = () => clock.textContent = new Date().toLocaleTimeString();
  tick(); setInterval(tick, 1000);
  notes.value = localStorage.getItem("notes") || "";
  notes.oninput = () => localStorage.setItem("notes", notes.value);
  fetch("steed://" + location.host + "/api/tabs").then(r => r.json()).then(data => {
    if (data.error) return;
    tabs.replaceChildren(...data.map(tab => {
      const item = document.createElement("li"), link = document.createElement("a");
      link.href = tab.url; link.textContent = tab.title || tab.url;
      item.append(link); return item;
    }));
  });
</script>
"""


class AppsPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pages = window.profile_manager.registry.start_pages
        self.context = token_hex(16)
        self.sequence = 0
        self.notice = self.error = ""
        self.disposed = False
        self.surface = HtmlSurface("apps", self, label="apps")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.surface)
        self.surface.command.connect(self.command)
        self.pages.changed.connect(self._changed)
        window.profile_manager.profile_changed.connect(self._changed)
        self.publish()

    @property
    def profile(self):
        return self.window.profile_manager.current_profile_name

    def _changed(self, *_):
        self.publish()

    def publish(self):
        if self.disposed:
            return
        apps = (
            [] if self.profile == "search" else self.pages.state(self.profile)["apps"]
        )
        self.surface.publish(
            {
                "context": self.context,
                "ack": self.sequence,
                "private": self.profile == "search",
                "profile": self.profile,
                "apps": apps,
                "starter": STARTER,
                "dark": self.window.profile_manager.registry.page_style.dark(
                    self.profile
                ),
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
            if action == "save":
                identity = self.pages.save_app(
                    self.profile,
                    str(command.get("name", "")),
                    str(command.get("html", "")),
                    command.get("id") or None,
                )
                self.notice = "App saved."
                if command.get("open"):
                    self.window._show_panel_app(identity)
            elif action == "delete":
                app = self.pages.app(self.profile, command.get("id"))
                if app is None:
                    raise ValueError("That app no longer exists.")
                reply = MessageBox.question(
                    self.window,
                    "Delete app",
                    f"Delete the panel app “{app['name']}”? Its HTML is removed. "
                    "Notes or data it stored stay in this profile's site data.",
                )
                if reply == MessageBox.StandardButton.Yes:
                    self.pages.remove_app(self.profile, app["id"])
                    self.notice = "App deleted."
            elif action == "open":
                if self.pages.app(self.profile, command.get("id")) is None:
                    raise ValueError("That app no longer exists.")
                self.window._show_panel_app(command["id"])
            elif action == "import":
                path, _ = QFileDialog.getOpenFileName(
                    self.window, "Import an app", "", "HTML files (*.html *.htm)"
                )
                if path:
                    html = Path(path).read_text(encoding="utf-8")
                    self.pages.save_app(self.profile, Path(path).stem[:60], html)
                    self.notice = "App imported."
            else:
                raise ValueError("Unsupported request.")
        except ValueError as error:
            self.error = str(error)
        except (OSError, UnicodeError):
            self.error = "The file could not be read as UTF-8 HTML."
        self.publish()

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.pages.changed.disconnect(self._changed)
        self.window.profile_manager.profile_changed.disconnect(self._changed)
        self.surface.dispose()
