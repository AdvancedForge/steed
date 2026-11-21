"""View → Your Pages…: your own steed:<name> tabs.

A page of yours is HTML and JavaScript you (or your assistant) write: a
dashboard of your watches and routines, a launcher for your skills, a board of
links, a small tool. Open it as steed:<name> in any tab, or make it your New
Tab. Its scripts reach Steed through fetch("/api/…"), each capability allowed
in Access → Local pages (never, ask, always).
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ui.html_dialog import HtmlSettingsDialog

API = """fetch("/api/<name>") from your page, each allowed in Access → Local pages:
  fetch?url=…   load a web address (no cookies)     tabs, history, bookmarks
  watches       pages you watch, with what changed   routines, skills
  run-skill?id=…   run-routine?id=…   open?url=…   notify?title=…&text=…"""

DASHBOARD = """<main style="max-width:880px;margin:40px auto;padding:0 20px;font-family:system-ui">
<h1>My dashboard</h1>
<section><h2>Watched pages</h2><ul id="watches"><li>Loading…</li></ul></section>
<section><h2>Routines</h2><ul id="routines"><li>Loading…</li></ul></section>
<section><h2>Skills</h2><div id="skills"></div></section>
</main>
<script>
const list = (id, items, line) => {
  const target = document.getElementById(id);
  target.replaceChildren(...(items.length ? items.map(line) : [Object.assign(document.createElement("li"), {textContent: "Nothing yet."})]));
};
const item = text => Object.assign(document.createElement("li"), {textContent: text});
const when = seconds => seconds ? new Date(seconds * 1000).toLocaleString() : "not yet";
fetch("/api/watches").then(r => r.json()).then(watches => list("watches", watches,
  w => item(`${w.title}: ${w.last_summary || "no change yet"} (${when(w.last_change)})`)));
fetch("/api/routines").then(r => r.json()).then(routines => list("routines", routines, r => {
  const li = item(`${r.name}, ${r.when}, last ran ${when(r.last_run)} `);
  const run = Object.assign(document.createElement("button"), {textContent: "Run now"});
  run.onclick = () => fetch("/api/run-routine?id=" + encodeURIComponent(r.id));
  li.append(run); return li;
}));
fetch("/api/skills").then(r => r.json()).then(skills => {
  document.getElementById("skills").replaceChildren(...skills.map(s => {
    const button = Object.assign(document.createElement("button"), {textContent: s.name, title: s.ask});
    button.onclick = () => fetch("/api/run-skill?id=" + encodeURIComponent(s.id));
    return button;
  }));
});
</script>"""

LAUNCHER = """<main style="max-width:640px;margin:12vh auto;padding:0 20px;font-family:system-ui;text-align:center">
<h1>What now?</h1>
<p>Your skills, one click each.</p>
<div id="skills" style="display:flex;flex-wrap:wrap;gap:10px;justify-content:center"></div>
</main>
<script>
fetch("/api/skills").then(r => r.json()).then(skills => {
  document.getElementById("skills").replaceChildren(...skills.map(s => {
    const button = Object.assign(document.createElement("button"), {textContent: s.name, title: s.ask});
    button.onclick = () => fetch("/api/run-skill?id=" + encodeURIComponent(s.id));
    return button;
  }));
});
</script>"""

BLANK = """<main style="max-width:720px;margin:40px auto;padding:0 20px;font-family:system-ui">
<h1>My page</h1>
<p>Write anything here. Scripts can use fetch("/api/…").</p>
</main>"""

TEMPLATES = (
    ("Dashboard: watches, routines, skills", "dashboard", "My dashboard", DASHBOARD),
    ("Launcher: your skills", "launch", "Launch", LAUNCHER),
    ("Blank page", "page", "My page", BLANK),
)

WRITER = (
    "Write a single self-contained HTML body (inline <style> and <script> only, "
    "no external files) for a page in the Steed browser, from this description. "
    "Its scripts can call these same-origin endpoints with fetch, which return "
    "JSON:\n"
    "/api/watches -> [{title,url,last_change,last_summary,paused}], "
    "/api/routines -> [{id,name,when,last_run,paused}], "
    "/api/skills -> [{id,name,ask}], /api/tabs, /api/history, /api/bookmarks -> "
    "[{title,url}], /api/fetch?url=… -> the web response, "
    "/api/run-skill?id=…, /api/run-routine?id=…, /api/open?url=…, "
    "/api/notify?title=…&text=…. Use the CSS variables var(--bg), var(--surface), "
    "var(--ink), var(--muted), var(--accent), var(--border) so it matches light "
    "and dark. Reply with the HTML only.\n\nDescription: {description}"
)


class YourPageEditor(HtmlSettingsDialog):
    def __init__(self, window, page=None, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.page = page
        self.asking = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Edit page" if page else "New page")
        page = page or {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Address: steed:<name>"))
        self.name = QLineEdit(page.get("name", ""))
        self.name.setPlaceholderText("dashboard")
        layout.addWidget(self.name)
        layout.addWidget(QLabel("Title"))
        self.title_field = QLineEdit(page.get("title", ""))
        self.title_field.setPlaceholderText("My dashboard")
        layout.addWidget(self.title_field)
        if not page:
            self.template = QComboBox()
            for label, name, title, html in TEMPLATES:
                self.template.addItem(label, (name, title, html))
            self.template.currentIndexChanged.connect(self._template)
            layout.addWidget(QLabel("Start from"))
            layout.addWidget(self.template)
        builder = QHBoxLayout()
        self.describe = QLineEdit()
        self.describe.setPlaceholderText(
            "Or describe it, e.g. my watched prices as cards, newest first"
        )
        builder.addWidget(self.describe, 1)
        write = QPushButton("Write it for me")
        write.clicked.connect(self.write_for_me)
        builder.addWidget(write)
        layout.addLayout(builder)
        layout.addWidget(QLabel("HTML and JavaScript"))
        self.html = QPlainTextEdit(page.get("html", ""))
        layout.addWidget(self.html, 1)
        api = QLabel(API)
        api.setTextFormat(Qt.TextFormat.PlainText)
        api.setProperty("role", "note")
        layout.addWidget(api)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        save = QPushButton("Save and open")
        save.setProperty("primary", True)
        save.clicked.connect(self.save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(save)
        row.addWidget(cancel)
        layout.addLayout(row)
        if not page:
            self._template()
        self.finished.connect(lambda _: self.asking and self.asking.stop())

    def _template(self, *_):
        name, title, html = self.template.currentData()
        self.name.setText(name)
        self.title_field.setText(title)
        self.html.setPlainText(html)

    def write_for_me(self):
        from services.agent_once import AgentOnce, agent_command

        description = self.describe.text().strip()
        command = agent_command(self.window.config)
        if not description:
            self.status.setText("Describe the page first.")
            return
        if command is None:
            self.status.setText("Add an assistant first (Agents → Add an Assistant…).")
            return
        self.status.setText("Writing…")
        self.asking = AgentOnce(
            command, WRITER.replace("{description}", description), self
        )
        self.asking.answered.connect(self._written)
        self.asking.failed.connect(self.status.setText)

    def _written(self, text):
        html = text.strip()
        if html.startswith("```"):  # Some assistants fence their answer.
            html = html.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        self.html.setPlainText(html)
        self.status.setText("Check it, then Save and open.")

    def save(self):
        pages = self.window.profile_manager.registry.start_pages
        profile = self.window.profile_manager.current_profile_name
        try:
            name = pages.save_your_page(
                profile,
                self.name.text(),
                self.title_field.text(),
                self.html.toPlainText(),
                replacing=self.page["name"] if self.page else None,
            )
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.accept()
        self.window._new_tab(url="steed:" + name)


class YourPagesDialog(HtmlSettingsDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Your pages")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Your own steed:<name> tabs: dashboards, launchers and small tools in "
            "HTML and JavaScript. Open one by typing its address, or set it as "
            "your New Tab in Settings. What its scripts may do follows Access → "
            "Local pages."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "note")
        layout.addWidget(intro)
        self.list = QListWidget()
        self.list.setProperty("empty", "No pages yet. Start with a dashboard.")
        self.list.itemDoubleClicked.connect(lambda _: self.open())
        self.list.currentItemChanged.connect(lambda *_: self._selection_changed())
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.selection_buttons = []
        for text, callback in (
            ("New page…", self.new),
            ("Open", self.open),
            ("Edit…", self.edit),
            ("Remove", self.remove),
        ):
            button = QPushButton(text)
            if text == "New page…":
                button.setProperty("primary", True)
            else:
                self.selection_buttons.append(button)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.refresh()

    def _pages(self):
        return self.window.profile_manager.registry.start_pages

    def _profile(self):
        return self.window.profile_manager.current_profile_name

    def refresh(self):
        self.list.clear()
        for page in self._pages().your_pages(self._profile()):
            item = QListWidgetItem(f"steed:{page['name']}  ·  {page['title']}")
            item.setData(Qt.ItemDataRole.UserRole, page["name"])
            self.list.addItem(item)
        self._selection_changed()

    def _selection_changed(self):
        for button in self.selection_buttons:
            button.setEnabled(self.list.currentItem() is not None)

    def selected(self):
        item = self.list.currentItem()
        if item is None:
            return None
        return self._pages().your_page(
            self._profile(), item.data(Qt.ItemDataRole.UserRole)
        )

    def new(self):
        editor = YourPageEditor(self.window)
        editor.finished.connect(lambda _: self.refresh())
        self.window._present_dialog(editor, modal=False)
        return editor

    def open(self):
        page = self.selected()
        if page is not None:
            self.window._new_tab(url="steed:" + page["name"])

    def edit(self):
        page = self.selected()
        if page is None:
            return None
        editor = YourPageEditor(self.window, page)
        editor.finished.connect(lambda _: self.refresh())
        self.window._present_dialog(editor, modal=False)
        return editor

    def remove(self):
        page = self.selected()
        if page is not None:
            self._pages().remove_your_page(self._profile(), page["name"])
            self.refresh()
