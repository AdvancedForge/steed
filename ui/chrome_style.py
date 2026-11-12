"""Shared look for native browser chrome: stroke icons and toolbar styling.

The toolbar stays native Qt so typing, focus, input methods and undo behave
exactly like any text field; this module makes it look like the HTML tab strip.
"""

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
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
    toolbar = mix(tint, "#141414" if dark else "#ffffff", 0.6 if dark else 0.7)
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
        "strip": mix(toolbar, "#000000", 0.28 if dark else 0.1),
        "menu": toolbar,
        "dark": dark,
        "glass": glass,
    }


def browser_colors(toolbar, dark, accent=None, glass=False, chosen=None):
    """chrome_colors with the user's fixed colours (page style "chrome") on
    top; the colours they leave alone are still derived from the toolbar."""
    chosen = chosen or {}
    colors = chrome_colors(
        chosen.get("toolbar", toolbar), dark, chosen.get("accent", accent), glass
    )
    colors["card"] = colors["field"]
    colors.update(chosen)
    return colors


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
#SteedToolbar QPushButton#WarningBadge {{
  max-width: 120px; padding: 0 11px 0 8px; font-weight: 700; font-size: 11px; letter-spacing: 1px;
  color: {"#ffb27d" if colors["dark"] else "#a8380a"};
  background: {"rgba(255,110,40,46)" if colors["dark"] else "rgba(230,90,20,30)"};
  border: 1px solid {"rgba(255,130,60,120)" if colors["dark"] else "rgba(210,80,20,110)"};
}}
#SteedToolbar QPushButton#WarningBadge:hover {{
  background: {"rgba(255,110,40,80)" if colors["dark"] else "rgba(230,90,20,55)"};
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


def frost_rule(selector, path):
    """Paint a frosted slice (a file Steed wrote) behind a bar, stretched."""
    if not path or any(c in path for c in '"\\\n'):
        return ""
    return f'{selector} {{ border-image: url("{path}") 0 0 0 0 stretch stretch; }}'


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
{frost_rule("#SteedToolbar", colors.get("frost_toolbar"))}
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
{frost_rule("QMenuBar", colors.get("frost_menu"))}
QStatusBar {{ background: {colors["toolbar"]}; color: {colors["muted"]}; border-top: 1px solid {colors["border"]}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel#WarningNotice {{
  color: {"#ffb27d" if colors["dark"] else "#a8380a"}; font-weight: 600;
  background: {"rgba(255,110,40,40)" if colors["dark"] else "rgba(230,90,20,26)"};
  border: 1px solid {"rgba(255,130,60,100)" if colors["dark"] else "rgba(210,80,20,90)"};
  border-radius: 9px; padding: 1px 10px; margin: 2px 6px;
}}
QSizeGrip {{ background: transparent; }}
"""


def menu_stylesheet(colors):
    """Native menus (the toolbar's ☰ and its sections) in the toolbar's colours."""
    return f"""
QMenu {{
  background: {colors.get("menu", colors["toolbar"])}; color: {colors["ink"]};
  border: 1px solid {colors["border"]}; padding: 5px;
}}
QMenu::item {{ padding: 6px 26px 6px 12px; border-radius: 6px; background: transparent; }}
QMenu::item:selected {{ background: {colors["hover"]}; color: {colors["ink"]}; }}
QMenu::item:disabled {{ color: {colors["muted"]}; }}
QMenu::separator {{ height: 1px; background: {colors["border"]}; margin: 5px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 6px; }}
""" + (glass_menu(colors) if colors.get("glass") else "")


def glass_menu(colors):
    """Native menus in glass mode: a lit, glossy panel. Square corners: a
    rounded one needs a see-through window, which not every desktop draws
    (the corners would show as a box)."""
    fill = colors.get("menu", colors["toolbar"])
    top = mix(fill, "#ffffff", 0.08 if colors["dark"] else 0.35)
    edge = mix(fill, "#ffffff", 0.22 if colors["dark"] else 0.7)
    return f"""
QMenu {{
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {fill});
  border: 1px solid {edge}; padding: 6px;
}}
QMenu::item {{ border-radius: 8px; }}
"""


# The glass tint shared by the toolbar and the current tab (tabs.css --pane).
PANE_ALPHA = {True: 26, False: 77}


def glass_canvas(image, width, height, regions, dark, panes=()):
    """The browser's bars as frosted glass over a picture that fills the
    whole width x height window, cropped as the New Tab page crops it (cover,
    centred), evenly blurred and toned so text stays readable. regions:
    {name: QRect} in window coordinates (a top bar, a side strip, bars over
    the page in compact mode); returns {name: QImage}. Regions in panes (the
    toolbar) get the light glass tint the current tab has too."""
    from math import ceil

    from PyQt6.QtCore import QRect, Qt

    smooth = Qt.TransformationMode.SmoothTransformation
    ignore = Qt.AspectRatioMode.IgnoreAspectRatio
    scale = max(width / image.width(), height / image.height())
    left = (image.width() * scale - width) / 2
    top = (image.height() * scale - height) / 2
    source = QRect(
        int(left / scale),
        int(top / scale),
        max(1, ceil(width / scale)),
        max(1, ceil(height / scale)),
    )
    canvas = image.copy(source).scaled(width, height, ignore, smooth)
    # The same factor both ways, so nothing smears into streaks.
    for factor in (14, 4):
        canvas = canvas.scaled(
            max(1, width // factor), max(1, height // factor), ignore, smooth
        )
    canvas = canvas.scaled(width, height, ignore, smooth).convertToFormat(
        QImage.Format.Format_RGB32
    )
    shown = [rect.intersected(canvas.rect()) for rect in regions.values()]
    shown = [rect for rect in shown if not rect.isEmpty()]
    samples = []
    for rect in shown:
        probe = canvas.copy(rect).scaled(16, 4, ignore, smooth)
        samples += [
            probe.pixelColor(x, y).lightnessF() for x in range(16) for y in range(4)
        ]
    light = sum(samples) / len(samples) if samples else 0.5
    painter = QPainter(canvas)
    if dark:  # Light text: darken bright pictures.
        alpha = min(0.7, max(0.15, 1 - 0.3 / max(light, 0.01)))
        painter.fillRect(canvas.rect(), QColor(0, 0, 0, int(alpha * 255)))
    else:  # Dark text: lighten dark pictures.
        alpha = min(0.7, max(0.15, (0.78 - light) / max(1 - light, 0.01)))
        painter.fillRect(canvas.rect(), QColor(255, 255, 255, int(alpha * 255)))
    painter.end()
    pieces = {}
    for name, rect in regions.items():
        piece = canvas.copy(rect)
        if name in panes:
            painter = QPainter(piece)
            painter.fillRect(
                piece.rect(), QColor(255, 255, 255, PANE_ALPHA[bool(dark)])
            )
            painter.end()
        pieces[name] = piece
    return pieces


def blurred(pixmap, factor=12):
    """A frosted (heavily blurred) copy of a pixmap, the same size."""
    from PyQt6.QtCore import Qt

    smooth = Qt.TransformationMode.SmoothTransformation
    ignore = Qt.AspectRatioMode.IgnoreAspectRatio
    size = pixmap.size()
    small = pixmap
    for step in (factor, factor // 3 or 1):
        small = small.scaled(
            max(1, size.width() // step), max(1, size.height() // step), ignore, smooth
        )
    return small.scaled(size, ignore, smooth)


def scrollbar_stylesheet(colors):
    """Native scroll bars that match the web ones (root.css): thin, rounded,
    in the browser's text colour, evenly inset, no arrows."""
    ink = QColor(colors["ink"])
    rgb = f"{ink.red()},{ink.green()},{ink.blue()}"
    return f"""
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 0; border: none; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0 4px; border: none; }}
QScrollBar::handle:vertical {{ background: rgba({rgb},62); border-radius: 2px; min-height: 36px; margin: 0 4px; }}
QScrollBar::handle:horizontal {{ background: rgba({rgb},62); border-radius: 2px; min-width: 36px; margin: 4px 0; }}
QScrollBar::handle:hover {{ background: rgba({rgb},107); }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""
