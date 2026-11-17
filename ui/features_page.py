"""steed:features: the engine's version, video codecs and web feature support."""

from secrets import token_hex

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from browser import engine_features
from ui.html_surface import HtmlSurface


class FeaturesPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.context = token_hex(16)
        self.sequence = 0
        self.disposed = False
        self.features = None
        self.probe = None
        self.surface = HtmlSurface("features", self, label="features")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.surface)
        self.surface.command.connect(self.command)
        window.profile_manager.registry.updates.changed.connect(self.publish)
        self.check()

    def check(self):
        """Test every feature again in a fresh private page."""
        if self.probe is not None:
            return
        self.probe = engine_features.FeatureProbe(self)
        self.probe.finished.connect(self._checked)
        self.probe.start()
        self.publish()

    def _checked(self, features):
        self.features = features
        self.probe = None
        self.publish()

    def publish(self):
        if self.disposed:
            return
        registry = self.window.profile_manager.registry
        self.surface.publish(
            {
                "context": self.context,
                "ack": self.sequence,
                "engine": engine_features.engine_summary(),
                "features": self.features,
                "checking": self.probe is not None,
                "updates": {
                    **registry.updates.state,
                    "summary": registry.updates.summary(),
                    "enabled": registry.updates.enabled,
                },
                "dark": registry.page_style.dark(
                    self.window.profile_manager.current_profile_name
                ),
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
        if command.get("action") == "check":
            self.check()
        elif command.get("action") == "updates":
            self.window.profile_manager.registry.updates.check()
        self.publish()

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.surface.dispose()
