"""Shared Qt WebEngine settings for profiles and pages."""

from PyQt6.QtWebEngineCore import QWebEngineSettings


def configure_web_settings(settings: QWebEngineSettings) -> None:
    """Apply the browser's existing media and rendering preferences."""
    attributes = QWebEngineSettings.WebAttribute
    for attribute in (
        attributes.PluginsEnabled,
        attributes.PdfViewerEnabled,
        attributes.FullScreenSupportEnabled,
        attributes.WebGLEnabled,
        attributes.Accelerated2dCanvasEnabled,
        attributes.AutoLoadImages,
    ):
        settings.setAttribute(attribute, True)
    settings.setAttribute(attributes.PlaybackRequiresUserGesture, False)
