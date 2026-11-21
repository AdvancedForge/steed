"""steed:access: what agents and local pages may do, by capability or preset."""

from secrets import token_hex

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from browser import access_levels
from ui.html_surface import HtmlSurface

AREA_TEXT = {
    "agents": (
        "Agents",
        "Any agent or model you use. Never: it can't. Ask: you approve each "
        "request. Always: no prompt (only for public sites and your Downloads "
        "folder). Applies to new requests; access already approved stays until "
        "the task ends.",
    ),
    "pages": (
        "Local pages",
        "Your custom New Tab and Welcome pages. They can ask Steed for data with "
        'fetch("steed://newtab/api/fetch?url=…"), /api/tabs, /api/history and '
        "/api/bookmarks. Ask shows a question the first time in a session.",
    ),
}


class AccessPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.config = window.config
        self.registry = window.profile_manager.registry
        self.context = token_hex(16)
        self.sequence = 0
        self.notice = self.error = ""
        self.disposed = False
        self.surface = HtmlSurface("access", self, label="access")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.surface)
        self.surface.command.connect(self.command)
        self.registry.settings_changed.connect(self.publish)
        self.publish()

    def publish(self):
        if self.disposed:
            return
        areas = []
        for area, (_, capabilities, _default) in access_levels.AREAS.items():
            title, text = AREA_TEXT[area]
            current = access_levels.values(self.config, area)
            areas.append(
                {
                    "id": area,
                    "title": title,
                    "text": text,
                    "preset": access_levels.preset_of(self.config, area),
                    "capabilities": [
                        {
                            "id": name,
                            "label": spec["label"],
                            "help": spec["help"],
                            "policy": current[name],
                        }
                        for name, spec in capabilities.items()
                    ],
                }
            )
        tasks = self.registry._tasks
        self.surface.publish(
            {
                "context": self.context,
                "ack": self.sequence,
                "areas": areas,
                "presets": [
                    {"id": name, "label": access_levels.PRESET_LABELS[name]}
                    for name in access_levels.PRESETS
                ],
                "policies": [
                    {"id": name, "label": access_levels.POLICY_LABELS[name]}
                    for name in access_levels.POLICIES
                ],
                "yolo": bool(tasks is not None and tasks.auto_approve_requests),
                "dark": self.registry.page_style.dark(
                    self.window.profile_manager.current_profile_name
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
        area = command.get("area")
        try:
            if command.get("action") == "preset":
                access_levels.apply_preset(self.config, area, command.get("preset"))
                self.notice = (
                    f"{AREA_TEXT[area][0]}: "
                    f"{access_levels.PRESET_LABELS[command['preset']]} applied."
                )
            elif command.get("action") == "set":
                access_levels.set_policy(
                    self.config, area, command.get("capability"), command.get("policy")
                )
                self.notice = "Saved."
            else:
                raise ValueError("Unsupported access request.")
            self.registry.settings_changed.emit()
        except (ValueError, KeyError) as error:
            self.error = str(error).strip("'") or "That change could not be saved."
        self.publish()

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.registry.settings_changed.disconnect(self.publish)
        self.surface.dispose()
