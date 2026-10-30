"""steed:memory: choose a memory/performance mode or tune each optimization."""

from secrets import token_hex

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import memory_modes
from ui.html_surface import HtmlSurface

REFRESH_MS = 3000


class MemoryPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.config = window.config
        self.registry = window.profile_manager.registry
        self.context = token_hex(16)
        self.sequence = 0
        self.notice = self.error = ""
        self.disposed = False
        self.surface = HtmlSurface("memory", self, label="memory")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.surface)
        self.surface.command.connect(self.command)
        self.registry.settings_changed.connect(self.publish)
        memory_modes.monitor().changed.connect(self.publish)
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.publish)
        self.timer.start()
        self.publish()

    def windows(self):
        from browser_window import BrowserWindow

        return [
            widget
            for widget in QApplication.topLevelWidgets()
            if isinstance(widget, BrowserWindow)
            and not widget._closing
            and widget.profile_manager.registry is self.registry
        ]

    def publish(self):
        if self.disposed or not self.isVisible() and self.surface.ready:
            return
        values = memory_modes.values(self.config)
        self.surface.publish(
            {
                "context": self.context,
                "ack": self.sequence,
                "mode": memory_modes.mode(self.config),
                "modes": [
                    {"id": name, "label": memory_modes.MODE_LABELS[name]}
                    for name in memory_modes.MODES
                ],
                "options": [
                    {
                        "key": key,
                        "label": spec["label"],
                        "help": spec["help"],
                        "value": values[key],
                        "choices": [
                            {"value": value, "label": label}
                            for value, label in spec["choices"]
                        ],
                        "presets": spec["presets"],
                    }
                    for key, spec in memory_modes.OPTIMIZATIONS.items()
                ],
                "usage": memory_modes.usage(self.windows()),
                "pressure": {
                    "low": memory_modes.monitor().low,
                    "available": None
                    if memory_modes.monitor().share is None
                    else round(memory_modes.monitor().share * 100),
                    "saver_active": memory_modes.monitor().low and values["auto_saver"],
                },
                "dark": self.window.profile_manager.registry.page_style.dark(
                    self.window.profile_manager.current_profile_name
                ),
                "notice": self.notice,
                "error": self.error,
            },
            force=True,
        )

    def _changed(self):
        """Every window and the process-wide policies follow the new values."""
        self.registry.settings_changed.emit()

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
            if action == "mode":
                memory_modes.apply_mode(self.config, command.get("mode"))
                self.notice = (
                    memory_modes.MODE_LABELS[command["mode"]] + " applied to Steed."
                )
                self._changed()
            elif action == "set":
                memory_modes.set_value(
                    self.config, command.get("key"), command.get("value")
                )
                self.notice = "Saved."
                self._changed()
            elif action == "free":
                freed = memory_modes.free_now(self.windows())
                self.notice = (
                    f"Unloaded {freed['unloaded']} background tab(s), put "
                    f"{freed['inspectors']} cookie inspector(s) to sleep and "
                    "returned freed memory."
                )
            elif action != "refresh":
                raise ValueError("Unsupported memory request.")
        except ValueError as error:
            self.error = str(error)
        self.publish()

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.timer.stop()
        self.registry.settings_changed.disconnect(self.publish)
        memory_modes.monitor().changed.disconnect(self.publish)
        self.surface.dispose()
