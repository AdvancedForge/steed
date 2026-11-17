"""Lumen: Steed's look. The page floats as a card in a field of soft light
coloured by what you are looking at; the bars rest on that light without
backgrounds or borders, and every change of colour flows.

How much of it runs is the "visual_effects" setting:
- "full": the light follows each site's colour, tabs cross-fade, things open
  and close with motion;
- "balanced": the same look with only colour fades (no snapshots kept);
- "minimal": flat colours and no motion, nothing extra in memory.
"""

from PyQt6.QtCore import QEasingCurve, QObject, QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QRadialGradient
from PyQt6.QtWidgets import QWidget

LEVELS = ("full", "balanced", "minimal")
# The window's own parts let the light through (the dark theme's window
# stylesheet would paint them all one grey).
WINDOW_STYLE = (
    "QMainWindow, #SteedCentral, #SteedColumn, #SteedContent, #SteedPages,"
    ' QDockWidget[lumenCard="true"], QDockWidget[lumenCard="true"] > QWidget'
    " { background: transparent; }"
    ' QDockWidget[lumenCard="true"]::title { background: transparent;'
    " padding: 6px 12px; text-align: left; }"
)
CARD_RADIUS = 12
CARD_MARGIN = 6
# One motion language: fast out, long settle ("stride").
STRIDE = QEasingCurve.Type.OutQuint
HOVER_MS, OPEN_MS, LIGHT_MS = 140, 240, 420


def level(config):
    value = config.get("visual_effects", "full")
    return value if value in LEVELS else "full"


def site_color(icon):
    """The colour a site's icon is known by (the strongest common hue), or
    None for grey or missing icons."""
    if icon is None or icon.isNull():
        return None
    image = icon.pixmap(32, 32).toImage().convertToFormat(QImage.Format.Format_ARGB32)
    buckets = {}
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            if color.alpha() < 160:
                continue
            hue, saturation, value, _ = color.getHsvF()
            if saturation < 0.28 or value < 0.22 or hue < 0:
                continue
            key = int(hue * 18) % 18
            weight = saturation * value
            total = buckets.setdefault(key, [0.0, 0.0, 0.0, 0.0])
            total[0] += color.redF() * weight
            total[1] += color.greenF() * weight
            total[2] += color.blueF() * weight
            total[3] += weight
    if not buckets:
        return None
    red, green, blue, weight = max(buckets.values(), key=lambda b: b[3])
    if weight < 1.5:
        return None
    return QColor.fromRgbF(red / weight, green / weight, blue / weight).name()


def theme_color(value):
    """A page's declared theme colour as #rrggbb, or None when missing,
    invalid, or too grey to light anything (white and black bars)."""
    import re

    if not isinstance(value, str) or len(value) > 64:
        return None
    css = re.fullmatch(
        r"\s*rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})(?:[\s,/]+([\d.]+%?))?\s*\)\s*",
        value,
    )
    if css:  # CSS rgb()/rgba(), which QColor does not read.
        red, green, blue = (min(255, int(part)) for part in css.groups()[:3])
        alpha = css.group(4)
        opacity = 1.0
        if alpha:
            opacity = float(alpha[:-1]) / 100 if alpha.endswith("%") else float(alpha)
        color = QColor(red, green, blue, round(max(0.0, min(1.0, opacity)) * 255))
    else:
        color = QColor(value.strip())
    if not color.isValid() or color.alpha() < 128:
        return None
    _, saturation, lightness, _ = color.getHslF()
    if saturation < 0.18 or not 0.12 < lightness < 0.9:
        return None
    return color.name()


class Light:
    """The field behind the chrome: a base colour and three soft glows placed
    as fractions of the window, so every part of the window (Qt bars, the
    HTML tab strip, the page's corners) paints the same field."""

    def __init__(self, base, glows):
        self.base = QColor(base)
        self.glows = [(x, y, r, QColor(c)) for x, y, r, c in glows]

    @classmethod
    def of(cls, colors, site=None, style=None):
        """The field for a window: a deepened base, lit from the top left by
        the browser's accent and from the top right (and faintly from below
        the page) by the site's colour."""
        dark = bool(colors.get("dark"))
        toolbar = QColor(colors["toolbar"])
        accent = QColor(colors["accent"])
        if site:
            site = QColor(site)
        else:  # No site colour: the accent turned a little, iridescent.
            hue, saturation, value, _ = accent.getHsvF()
            site = QColor.fromHsvF(((max(hue, 0) + 0.11) % 1.0), saturation, value)
        if dark:
            base = _mix(toolbar, QColor("#05070a"), 0.5)
            strength = (0.3, 0.36, 0.16)
        else:
            base = _mix(toolbar, QColor("#ffffff"), 0.25)
            strength = (0.34, 0.4, 0.18)

        def tinted(color, alpha):
            color = QColor(color)
            if dark:  # Glows are light, not paint: lift dark colours.
                hue, saturation, value, _ = color.getHsvF()
                color = QColor.fromHsvF(
                    max(hue, 0), min(1, saturation * 1.05), max(value, 0.62)
                )
            color.setAlphaF(alpha)
            return color

        # Style → Lumen: stronger or softer light, and fixed colours.
        style = style or {}
        scale = style.get("light", 100) / 100
        strength = tuple(min(1.0, s * scale) for s in strength)
        fixed = style.get("light_colors", {})
        if "base" in fixed:
            base = QColor(fixed["base"])
        return cls(
            base,
            [
                (0.1, -0.3, 0.42, tinted(fixed.get("left", accent), strength[0])),
                (0.8, -0.34, 0.5, tinted(fixed.get("right", site), strength[1])),
                (0.5, 1.2, 0.8, tinted(fixed.get("below", site), strength[2])),
            ],
        )

    @classmethod
    def flat(cls, colors, style=None):
        """Minimal effects: the base colour alone."""
        base = (style or {}).get("light_colors", {}).get("base", colors["toolbar"])
        return cls(base, [(0.5, 0.5, 0.5, QColor(0, 0, 0, 0))] * 3)

    def key(self):
        """What the field looks like, to the 8-bit colour."""
        name = QColor.NameFormat.HexArgb
        return (
            self.base.name(name),
            tuple(
                (round(x, 3), round(y, 3), round(r, 3), c.name(name))
                for x, y, r, c in self.glows
            ),
        )

    def same(self, other):
        return other is not None and self.key() == other.key()

    def blend(self, other, amount):
        def mixed(a, b):
            return QColor.fromRgbF(
                *(
                    a.getRgbF()[i] + (b.getRgbF()[i] - a.getRgbF()[i]) * amount
                    for i in range(4)
                )
            )

        return Light(
            mixed(self.base, other.base),
            [
                (
                    x1 + (x2 - x1) * amount,
                    y1 + (y2 - y1) * amount,
                    r1 + (r2 - r1) * amount,
                    mixed(c1, c2),
                )
                for (x1, y1, r1, c1), (x2, y2, r2, c2) in zip(
                    self.glows, other.glows, strict=True
                )
            ],
        )

    def paint(self, painter, rect, size, origin=None):
        """Fill rect (in the painter's coordinates) with the field of a window
        of `size`, the painter's (0, 0) being `origin` in the window."""
        origin = origin or QPointF(0, 0)
        painter.fillRect(rect, self.base)
        width, height = size.width(), size.height()
        reach = max(width, height)
        for x, y, r, color in self.glows:
            centre = QPointF(x * width - origin.x(), y * height - origin.y())
            gradient = QRadialGradient(centre, max(1.0, r * reach))
            clear = QColor(color)
            clear.setAlpha(0)
            gradient.setColorAt(0, color)
            gradient.setColorAt(0.55, _faded(color, 0.45))
            gradient.setColorAt(1, clear)
            painter.fillRect(rect, gradient)

    def css(self):
        """The same field for an HTML part (tabs.js places it)."""
        return {
            "base": self.base.name(),
            "glows": [
                [x, y, r, color.name(QColor.NameFormat.HexArgb)]
                for x, y, r, color in self.glows
            ],
        }


def _mix(first, second, amount):
    a, b = first.getRgbF(), second.getRgbF()
    return QColor.fromRgbF(*(a[i] + (b[i] - a[i]) * amount for i in range(3)))


def _faded(color, share):
    color = QColor(color)
    color.setAlphaF(color.alphaF() * share)
    return color


class CardCorner(QWidget):
    """One rounded corner of the page card: paints the light outside the
    curve over the page (pages are drawn by the engine and cannot be clipped)."""

    def __init__(self, window, corner):
        super().__init__(window)
        self.window_ = window
        self.corner = corner  # "tl", "tr", "bl", "br"
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.resize(CARD_RADIUS, CARD_RADIUS)

    def paintEvent(self, event):
        if getattr(self.window_, "light", None) is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.width()
        x = 0 if "l" in self.corner else -r
        y = 0 if "t" in self.corner else -r
        curve = QPainterPath()
        curve.addRoundedRect(QRectF(x, y, 2 * r, 2 * r), r, r)
        outside = QPainterPath()
        outside.addRect(QRectF(self.rect()))
        painter.setClipPath(outside.subtracted(curve))
        origin = QPointF(self.mapTo(self.window_, self.rect().topLeft()))
        self.window_.paint_backdrop(painter, QRectF(self.rect()), origin)


class LightAnimator(QObject):
    """Flows a window's light from one field to the next."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(STRIDE)
        self.animation.valueChanged.connect(self._step)
        self.start = self.target = None
        self.goal = None  # Where the light is going (or is).

    def go(self, light, animate=True):
        self.goal = light
        current = getattr(self.window, "light", None)
        self.animation.stop()
        if current is None or not animate:
            self._show(light)
            return
        self.start, self.target = current, light
        self.animation.setDuration(LIGHT_MS)
        self.animation.start()

    def _step(self, amount):
        if self.start is not None:
            self._show(self.start.blend(self.target, float(amount)))

    def _show(self, light):
        self.window.light = light
        self.window.light_changed()


def apply_font(app, choice):
    """Style → Lumen font: Steed's typeface, or the desktop's own."""
    if not hasattr(app, "desktop_font"):
        app.desktop_font = None
    if choice == "system":
        if app.desktop_font is not None and app.font() != app.desktop_font:
            app.setFont(app.desktop_font)
    else:
        apply_ui_font(app)


def apply_ui_font(app):
    """Steed's typeface for every native part, at the desktop's size, a
    touch heavier than regular so small text stays crisp."""
    from PyQt6.QtGui import QFont, QFontDatabase

    from browser.webengine import UI_FONT

    if UI_FONT not in QFontDatabase.families():
        return
    if getattr(app, "desktop_font", None) is None:
        app.desktop_font = QFont(app.font())
    if app.font().family() == UI_FONT:
        return
    font = QFont(app.font())
    font.setFamily(UI_FONT)
    font.setWeight(QFont.Weight.Medium)
    app.setFont(font)


class Dissolve(QWidget):
    """What was shown, fading away over what is shown now (tab switches)."""

    def __init__(self, parent, picture, duration=OPEN_MS):
        super().__init__(parent)
        self.picture = picture
        self.opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        animation = QVariantAnimation(self)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(self._step)
        animation.finished.connect(self.deleteLater)
        animation.start()

    def _step(self, value):
        self.opacity = float(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(self.opacity)
        painter.drawPixmap(0, 0, self.picture)


class Glide(QObject):
    """Toolbar motion: a capsule that fades in under a hovered button (and
    deepens while pressed), and a soft glow around the focused address
    field, in the accent colour."""

    def __init__(self, parent):
        super().__init__(parent)
        self.levels = {}  # button -> [shown, animation]
        self.hover = QColor(255, 255, 255, 22)
        self.pressed = QColor(255, 255, 255, 38)
        self.field = None
        self.glow = None

    def set_colors(self, colors):
        dark = bool(colors.get("dark"))
        self.hover = QColor(255, 255, 255, 24) if dark else QColor(255, 255, 255, 150)
        self.pressed = QColor(255, 255, 255, 40) if dark else QColor(255, 255, 255, 215)
        if self.glow is not None:
            accent = QColor(colors.get("accent", "#8caaff"))
            accent.setAlpha(150)
            self.glow.setColor(accent)

    def attach(self, button):
        if button not in self.levels:
            self.levels[button] = [0.0, None]
            button.installEventFilter(self)
            # Holds the table itself: this can run after the helper is gone.
            levels = self.levels
            button.destroyed.connect(lambda *_: levels.pop(button, None))

    def attach_field(self, field):
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect

        if self.field is field:
            return
        self.field = field
        self.glow = QGraphicsDropShadowEffect(field)
        self.glow.setOffset(0, 0)
        self.glow.setBlurRadius(0)
        self.glow.setColor(QColor(140, 170, 255, 150))
        field.setGraphicsEffect(self.glow)
        field.installEventFilter(self)

    def detach(self):
        for button in list(self.levels):
            button.removeEventFilter(self)
            button.update()
        self.levels.clear()
        if self.field is not None:
            self.field.removeEventFilter(self)
            self.field.setGraphicsEffect(None)
            self.field = self.glow = None

    def _to(self, button, target, duration=HOVER_MS):
        entry = self.levels.get(button)
        if entry is None:
            return
        if entry[1] is not None:
            entry[1].stop()
        animation = QVariantAnimation(self)
        animation.setStartValue(entry[0])
        animation.setEndValue(target)
        animation.setDuration(duration)
        animation.setEasingCurve(STRIDE)

        def step(value):
            entry[0] = float(value)
            button.update()

        animation.valueChanged.connect(step)
        entry[1] = animation
        animation.start()

    def _glow(self, focused):
        if self.glow is None:
            return
        animation = QVariantAnimation(self)
        animation.setStartValue(self.glow.blurRadius())
        animation.setEndValue(22.0 if focused else 0.0)
        animation.setDuration(OPEN_MS)
        animation.setEasingCurve(STRIDE)
        animation.valueChanged.connect(
            lambda v: self.glow and self.glow.setBlurRadius(v)
        )
        self._glowing = animation
        animation.start()

    def eventFilter(self, watched, event):
        from PyQt6.QtCore import QEvent

        kind = event.type()
        levels = getattr(self, "levels", None)
        if levels is None:  # Being torn down.
            return False
        if watched is getattr(self, "field", None):
            if kind == QEvent.Type.FocusIn:
                self._glow(True)
            elif kind == QEvent.Type.FocusOut:
                self._glow(False)
            return False
        if kind == QEvent.Type.Enter and watched.isEnabled():
            self._to(watched, 1.0)
        elif kind == QEvent.Type.Leave:
            self._to(watched, 0.0, OPEN_MS)
        elif kind == QEvent.Type.MouseButtonPress:
            self._to(watched, 1.6, 90)
        elif kind == QEvent.Type.MouseButtonRelease:
            self._to(watched, 1.0 if watched.underMouse() else 0.0)
        elif kind == QEvent.Type.Paint:
            shown = levels.get(watched, [0.0])[0]
            if shown > 0.001 and watched.isEnabled():
                painter = QPainter(watched)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                if shown > 1.0:
                    color = _between(self.hover, self.pressed, shown - 1.0)
                else:
                    color = QColor(self.hover)
                    color.setAlphaF(self.hover.alphaF() * shown)
                painter.setBrush(color)
                rect = QRectF(watched.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
                # Grows a touch as it appears.
                inset = (1.0 - min(shown, 1.0)) * 3
                rect = rect.adjusted(inset, inset, -inset, -inset)
                radius = min(rect.width(), rect.height()) / 2
                painter.drawRoundedRect(rect, radius, radius)
                painter.end()
        return False


def _between(first, second, amount):
    amount = max(0.0, min(1.0, amount))
    return QColor.fromRgbF(
        *(
            first.getRgbF()[i] + (second.getRgbF()[i] - first.getRgbF()[i]) * amount
            for i in range(4)
        )
    )


def open_window(window, rise=6):
    """A popup arrives: fades in while settling up into place. (Desktops
    that cannot fade windows just show it.)"""
    from PyQt6.QtCore import QPoint

    final = window.pos()
    window.setWindowOpacity(0.0)
    animation = QVariantAnimation(window)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setDuration(OPEN_MS)
    animation.setEasingCurve(STRIDE)

    def step(value):
        value = float(value)
        window.setWindowOpacity(value)
        window.move(final + QPoint(0, round((1.0 - value) * rise)))

    animation.valueChanged.connect(step)
    animation.finished.connect(
        lambda: (window.setWindowOpacity(1.0), window.move(final))
    )
    animation.start()
    window._opening = animation
