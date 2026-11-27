"""Readable colours: WCAG contrast, and a colour moved (lighter or darker,
keeping its hue) just far enough to read against its backgrounds.

Used for the colours Steed derives from a New Tab photo or a toolbar colour,
so a pale, dark or vivid photo never leaves text, links or buttons hard to
read: 4.5:1 for text (WCAG AA), 3:1 for large text and icons.
"""

from PyQt6.QtGui import QColor

TEXT = 4.5
LARGE = 3.0
DARK_TEXT = "#141922"
LIGHT_TEXT = "#ffffff"


def luminance(color):
    """WCAG relative luminance of a #rrggbb colour (0 black … 1 white)."""
    found = QColor(color)

    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(found.red())
        + 0.7152 * channel(found.green())
        + 0.0722 * channel(found.blue())
    )


def contrast(first, second):
    """WCAG contrast ratio, 1 (none) to 21 (black on white)."""
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def hsl(color):
    """(hue, saturation, lightness), each 0-1; a grey's hue is 0."""
    hue, saturation, lightness, _ = QColor(color).getHslF()
    return max(hue, 0.0), saturation, lightness


def from_hsl(hue, saturation, lightness):
    clamp = lambda value: min(1.0, max(0.0, value))  # noqa: E731
    return QColor.fromHslF(clamp(hue), clamp(saturation), clamp(lightness)).name()


def is_dark(color):
    return luminance(color) < 0.18


def readable(color, against, ratio=TEXT):
    """color, or the nearest colour of its hue (lighter on dark backgrounds,
    darker on light ones) that reaches ratio against each of against."""
    against = [against] if isinstance(against, str) else list(against)

    def worst(candidate):
        return min(contrast(candidate, other) for other in against)

    if worst(color) >= ratio:
        return color
    hue, saturation, lightness = hsl(color)
    step = 0.01 if is_dark(against[0]) else -0.01
    candidate = color
    for _ in range(101):
        lightness += step
        candidate = from_hsl(hue, saturation, lightness)
        if worst(candidate) >= ratio or not 0.0 < lightness < 1.0:
            break
    return candidate


def under_text(color, background, ratio=TEXT):
    """color, moved away from background (darker on a light one, lighter on
    a dark one) until white or near-black text on it reads: for buttons and
    selections in the accent. Moving away keeps it apart from background."""
    hue, saturation, lightness = hsl(color)
    step = 0.01 if is_dark(background) else -0.01
    candidate = color
    for _ in range(101):
        if contrast(text_on(candidate), candidate) >= ratio:
            break
        lightness += step
        if not 0.0 <= lightness <= 1.0:
            break
        candidate = from_hsl(hue, saturation, lightness)
    return candidate


def text_on(color):
    """White or near-black, whichever reads better on color."""
    return (
        LIGHT_TEXT
        if contrast(LIGHT_TEXT, color) >= contrast(DARK_TEXT, color)
        else DARK_TEXT
    )
