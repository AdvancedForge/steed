"""Your own keyboard shortcuts for the browser's commands.

Every command in the menus can take a different shortcut, or none. Changes
are kept in config.json ("shortcuts": {command name: "Ctrl+Shift+K" or ""})
by the command's name as the menus show it, and apply to every window; a
shortcut taken from another command leaves that one without. Reset brings
back Steed's own.
"""

from PyQt6.QtGui import QKeySequence

from ui.menu_layout import label


def commands(window):
    """[(name, action)] for every command in the window's menus, in menu order."""
    found, seen = [], set()

    def walk(menu):
        for action in menu.actions():
            if action.isSeparator():
                continue
            if action.menu() is not None:
                walk(action.menu())
                continue
            name = label(action)
            if name and name not in seen:
                seen.add(name)
                found.append((name, action))

    for menu in window._layout_menus.values():
        walk(menu)
    return found


def _defaults(action):
    """Steed's own shortcuts for a command (some have more than one)."""
    if action.property("steed_default_shortcuts") is None:
        action.setProperty(
            "steed_default_shortcuts",
            [
                k.toString(QKeySequence.SequenceFormat.PortableText)
                for k in action.shortcuts()
            ],
        )
    return list(action.property("steed_default_shortcuts") or [])


# Commands that were renamed: a shortcut you set keeps working.
RENAMED = {
    "Pop Out Search Profile (Incognito)": "New Incognito Window",
    "Switch to Search Profile (Incognito)": "Switch to Incognito",
}


def overrides(config):
    raw = config.get("shortcuts", {})
    found = (
        {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}
        if isinstance(raw, dict)
        else {}
    )
    for old, new in RENAMED.items():
        if old in found and new not in found:
            found[new] = found.pop(old)
    return found


def apply(window):
    """Steed's shortcuts, then yours, on this window's commands."""
    mine = overrides(window.config)
    for name, action in commands(window):
        defaults = _defaults(action)
        if name in mine:
            action.setShortcut(QKeySequence(mine[name]))
        else:
            action.setShortcuts([QKeySequence(k) for k in defaults])


def owner(window, sequence, besides=""):
    """The command that already has this shortcut (other than besides)."""
    wanted = QKeySequence(sequence)
    for name, action in commands(window):
        if name != besides and not wanted.isEmpty() and wanted in action.shortcuts():
            return name
    return None


def change(window, name, sequence):
    """Give a command a shortcut ("" for none); a command that had it loses
    it. Every window follows. Returns the name of that other command."""
    from PyQt6.QtWidgets import QApplication

    text = QKeySequence(sequence).toString(QKeySequence.SequenceFormat.PortableText)
    mine = overrides(window.config)
    taken = owner(window, text, besides=name) if text else None
    if taken:
        mine[taken] = ""
    defaults = {n: _defaults(a) for n, a in commands(window)}
    if [text] == defaults.get(name):
        mine.pop(name, None)
    else:
        mine[name] = text
    window.config.set("shortcuts", mine)
    for other in QApplication.topLevelWidgets():
        if hasattr(other, "_layout_menus") and not getattr(other, "_closing", True):
            apply(other)
    return taken


def reset(window, name=None):
    """Steed's own shortcut back (for one command, or all)."""
    from PyQt6.QtWidgets import QApplication

    mine = overrides(window.config)
    if name is None:
        mine = {}
    else:
        mine.pop(name, None)
    window.config.set("shortcuts", mine)
    for other in QApplication.topLevelWidgets():
        if hasattr(other, "_layout_menus") and not getattr(other, "_closing", True):
            apply(other)
