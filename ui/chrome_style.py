"""Shared look for native browser chrome: stroke icons and toolbar styling.

The toolbar stays native Qt so typing, focus, input methods and undo behave
exactly like any text field; this module makes it look like the HTML tab strip.
"""

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

# 24×24 stroke icons drawn for Steed (round caps, 2 px).
ICONS = {
    "back": '<path d="M15 5l-7 7 7 7"/>',
    "forward": '<path d="M9 5l7 7-7 7"/>',
    "reload": '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v5h-5"/>',
    "home": '<path d="M4 11l8-7 8 7"/><path d="M6 10v9h12v-9"/><path d="M10 19v-5h4v5"/>',
    "menu": '<path d="M5 7h14M5 12h14M5 17h14"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M16 7l3 3M14 9l2 2"/>',
    "zoom": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "lock": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "unlocked": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/><path d="M12 14.5v2"/>',
    "internal": '<circle cx="12" cy="12" r="8"/><path d="M12 8v5M12 16v.5"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "history": '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    "star": '<path d="M12 4l2.4 5 5.4.6-4 3.7 1.1 5.4L12 16l-4.9 2.7 1.1-5.4-4-3.7 5.4-.6z"/>',
    "tab": '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M8 6v4"/>',
    "puzzle": '<path d="M5 8h3.5a2 2 0 1 1 4 0H16v3.5a2 2 0 1 1 0 4V19H5z"/>',
    "go": '<path d="M5 12h14M13 6l6 6-6 6"/>',
}


def icon(name, color, size=20):
    """Render one stroke icon in a colour, sharp on high-DPI screens."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round">{ICONS[name]}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    result = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(QSize(size * scale, size * scale))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(pixmap)
    return result


def mix(first, second, amount):
    """Blend two colours; amount is the share of the second colour."""
    a, b = QColor(first), QColor(second)
    return QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
    ).name()


def tint_colors(tint, dark):
    """A toolbar and accent from a picture's colour: a pale (or deep) wash of
    it, and a stronger version for highlights."""
    toolbar = mix(tint, "#141414" if dark else "#ffffff", 0.78 if dark else 0.8)
    accent = mix(tint, "#ffffff" if dark else "#000000", 0.25)
    return toolbar, accent


def chrome_colors(toolbar, dark, accent=None, glass=False):
    """Derive every toolbar colour from the toolbar background."""
    ink = "#e8edf6" if dark else "#202b40"
    return {
        "toolbar": toolbar,
        "ink": ink,
        "muted": mix(ink, toolbar, 0.45),
        "field": mix(toolbar, "#000000" if dark else "#ffffff", 0.35 if dark else 0.7),
        "hover": mix(toolbar, ink, 0.1),
        "pressed": mix(toolbar, ink, 0.18),
        "border": mix(toolbar, ink, 0.16),
        "accent": accent or ("#8caaff" if dark else "#355ed6"),
        "dark": dark,
        "glass": glass,
    }


def toolbar_stylesheet(colors, density="comfortable"):
    compact = density == "compact"
    button = 28 if compact else 32
    radius = 14 if compact else 16
    return f"""
#SteedToolbar {{ background: {colors["toolbar"]}; border-bottom: 1px solid {colors["border"]}; }}
#SteedToolbar QWidget#ToolbarGroup {{ background: transparent; }}
#SteedToolbar QPushButton {{
  background: transparent; border: none; border-radius: {button // 2}px;
  min-width: {button}px; max-width: {button}px; min-height: {button}px; max-height: {button}px;
  padding: 0; color: {colors["ink"]};
}}
#SteedToolbar QPushButton:hover:enabled {{ background: {colors["hover"]}; }}
#SteedToolbar QPushButton:pressed {{ background: {colors["pressed"]}; }}
#SteedToolbar QPushButton:disabled {{ color: {colors["muted"]}; }}
#SteedToolbar QPushButton::menu-indicator {{ image: none; width: 0; }}
#SteedToolbar QPushButton#TextButton {{
  max-width: 200px; padding: 0 10px; font-weight: 600; font-size: 12px;
}}
#SteedToolbar QLineEdit {{
  background: {colors["field"]}; color: {colors["ink"]};
  border: 1px solid transparent; border-radius: {radius}px;
  padding: {3 if compact else 5}px 12px; min-height: {button - 8}px;
  selection-background-color: {colors["accent"]};
  selection-color: {"#142440" if colors["dark"] else "#ffffff"};
}}
#SteedToolbar QLineEdit:hover {{ border-color: {colors["border"]}; }}
#SteedToolbar QLineEdit:focus {{ border: 2px solid {colors["accent"]}; padding: {2 if compact else 4}px 11px; }}
""" + (glass_toolbar(colors, radius) if colors.get("glass") else "")


def glass_toolbar(colors, radius):
    """Liquid glass: a glossy toolbar and a frosted, lit-from-above field."""
    dark, toolbar = colors["dark"], colors["toolbar"]
    top = mix(toolbar, "#ffffff", 0.1 if dark else 0.55)
    if dark:
        field = "stop:0 rgba(255,255,255,40), stop:1 rgba(255,255,255,12)"
        edge, low, hover = "rgba(255,255,255,70)", "rgba(255,255,255,20)", "30"
    else:
        field = "stop:0 rgba(255,255,255,215), stop:1 rgba(255,255,255,120)"
        edge, low, hover = "rgba(255,255,255,240)", colors["border"], "120"
    return f"""
#SteedToolbar {{
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {toolbar});
  border-bottom: 1px solid {low};
}}
#SteedToolbar QPushButton {{ border: 1px solid transparent; }}
#SteedToolbar QPushButton:hover:enabled {{
  background: rgba(255,255,255,{hover}); border: 1px solid {edge};
}}
#SteedToolbar QLineEdit {{
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, {field});
  border: 1px solid {edge}; border-bottom-color: {low}; border-radius: {radius + 2}px;
}}
#SteedToolbar QLineEdit:hover {{ border-color: {edge}; border-bottom-color: {low}; }}
"""


def popup_stylesheet(colors):
    return f"""
QListView {{
  background: {colors["field"]}; color: {colors["ink"]};
  border: 1px solid {colors["border"]}; border-radius: 10px; outline: none;
}}
QListView::item {{ border-radius: 8px; padding: 2px 6px; }}
QListView::item:selected, QListView::item:hover {{ background: {colors["hover"]}; color: {colors["ink"]}; }}
"""


def bar_stylesheet(colors):
    """Menu and status bars in the toolbar colour, without heavy borders."""
    return f"""
QMenuBar {{ background: {colors["toolbar"]}; color: {colors["ink"]}; border: none; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 4px 9px; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {colors["hover"]}; }}
QStatusBar {{ background: {colors["toolbar"]}; color: {colors["muted"]}; border-top: 1px solid {colors["border"]}; }}
QStatusBar::item {{ border: none; }}
"""
