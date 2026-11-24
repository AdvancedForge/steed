"""How the browser's menus read: related items together, an icon for the
ones people look for, and labels written one way.

Menus are built where their actions live (browser_window); this only orders
them. Anything not named here keeps its place at the end, before Exit.
"""

from PyQt6 import sip
from PyQt6.QtWidgets import QMenu

GROUPS = {
    "general": (
        ("Print…",),
        ("Settings…", "Customize New Tab…", "Layout Settings…", "Keyboard Shortcuts…"),
        ("Passwords…", "Downloads…", "Extensions"),
        ("Import from Another Browser…", "Clear Browsing Data…"),
    ),
    "tabs": (
        (
            "New Tab",
            "New Window",
            "Restore Closed Tab",
            "Reopen Closed Window",
            "Close Tab",
        ),
        ("Back", "Forward", "Reload", "Home"),
        ("Tab Groups", "Saved Groups", "Workspaces"),
        ("Bookmark This Page…", "Manage Bookmarks…"),
        ("Organize Tabs with Agent…", "Compare Tabs with Agent…"),
    ),
    "view": (
        ("Find in Page…", "Command Palette…"),
        ("Zoom In", "Zoom Out", "Reset Zoom", "Reset Zoom (100%)"),
        (
            "Reader View",
            "Read Aloud",
            "Translate Page",
            "Save to Reading List",
            "Reading List…",
        ),
        ("Full Screen", "Bookmarks Bar"),
        (
            "Save Page As…",
            "Take Screenshot",
            "Split View with Next Tab",
            "Close Split View",
        ),
        ("Page Style…", "Reset Layout"),
        ("Your Pages…", "Packs…", "Your Commands"),
    ),
    "cookies": (
        (
            "Browsing History…",
            "Search What You've Read…",
            "Find a Page I Saw…",
            "Clear Browsing History…",
        ),
    ),
    "agents": (
        (
            "Agent Sidebar",
            "Skills",
            "Add an Assistant…",
            "Helper Assistant",
            "Pause All Agents",
        ),
        ("Automations", "Watches…", "Data Panels"),
        ("Agent Chat Tab", "Website Chats", "Agent Activity"),
    ),
}
# Items gathered into sub-menus near the end, out of the way of everyday use:
# each menu keeps what people reach for, and every command stays in the
# command palette and keeps its shortcut.
SUBMENUS = {
    "general": (
        (
            "More Tools",
            (
                ("Bitwarden…", "Fill Login", "Dictation"),
                ("Sync…", "Search Keywords…", "User Scripts & Styles…"),
                ("Ad Blocking…", "Website Permissions…"),
            ),
        ),
        ("Help", (("Welcome to Steed", "Crash Log…"),)),
    ),
    "tabs": (
        (
            "More Tab Tools",
            (
                ("Next Tab", "Previous Tab"),
                ("Save All Tabs as Group…", "Session History…"),
                ("Send to Your Phone…", "Group My Recent History into Topics…"),
            ),
        ),
        ("Site Apps", (("Install This Site as an App…", "Site Apps…"),)),
    ),
    "view": (
        (
            "Keyboard Navigation",
            (
                ("Find Next", "Find Previous", "Go to Address Bar"),
                ("Search Tabs…", "Search History…"),
                (
                    "Follow a Link with the Keyboard",
                    "Open a Link in a New Tab with the Keyboard",
                ),
            ),
        ),
        (
            "Appearance",
            (
                ("Liquid Glass", "Compact Mode", "Tabs on the Side"),
                ("Show Top Menu Bar", "Show Bottom Status Bar", "App Panel"),
                ("Reset Page Style…", "Recover Previous Layout"),
                ("Import Layout…", "Export Layout…"),
            ),
        ),
        ("Media", (("Play or Pause Media", "Mini Player"),)),
        (
            "Advanced",
            (
                ("View Page Source", "Developer Tools"),
                ("Memory & Performance…", "Engine & Features…"),
            ),
        ),
    ),
    "agents": (
        (
            "Access and Setup",
            (
                ("Agent Connection Setup…", "Enable Local Agent Access"),
                (
                    "Access & Permissions…",
                    "Task Permissions…",
                    "App Connections…",
                    "Agent File Library…",
                ),
                ("Approve All Agent Requests (unsafe)",),
            ),
        ),
    ),
}
LAST = ("Close Window", "Quit Steed")

# Icons (drawn in menu.js) for the items people look for.
ICONS = {
    "New Tab": "plus",
    "Restore Closed Tab": "undo",
    "Close Tab": "close",
    "Back": "back",
    "Forward": "forward",
    "Reload": "reload",
    "Home": "home",
    "Tab Groups": "layers",
    "Saved Groups": "layers",
    "Session History…": "clock",
    "Bookmark This Page…": "star",
    "Manage Bookmarks…": "bookmarks",
    "Organize Tabs with Agent…": "spark",
    "Compare Tabs with Agent…": "columns",
    "Settings…": "gear",
    "Print…": "print",
    "Keyboard Shortcuts…": "keyboard",
    "New Window": "window",
    "Full Screen": "expand",
    "Bookmarks Bar": "bookmarks",
    "Customize New Tab…": "brush",
    "Layout Settings…": "layout",
    "Passwords…": "key",
    "Downloads…": "download",
    "Dictation": "mic",
    "Extensions": "puzzle",
    "Import from Another Browser…": "import",
    "Ad Blocking…": "shield",
    "Website Permissions…": "lock",
    "Clear Browsing Data…": "trash",
    "Quit Steed": "exit",
    "Find in Page…": "search",
    "Command Palette…": "command",
    "Zoom In": "zoom-in",
    "Zoom Out": "zoom-out",
    "Reader View": "book",
    "Read Aloud": "speaker",
    "Translate Page": "translate",
    "Take Screenshot": "camera",
    "Save Page As…": "save",
    "Page Style…": "brush",
    "Developer Tools": "code",
    "Agent Sidebar": "spark",
    "Agent Chat Tab": "chat",
    "Watches…": "eye",
    "Routines…": "clock",
    "Website Chats": "chat",
    "Access & Permissions…": "lock",
    "Access and Setup": "gear",
    "New Profile…": "user",
    "Switch Profile…": "users",
    "Browsing History…": "clock",
    "Search What You've Read…": "search",
    "View Cookies…": "cookie",
}


def label(action):
    """The text as shown: "&&" is a literal "&", "..." is "…"."""
    text = action.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
    return text[:-3] + "…" if text.endswith("...") else text


def arrange(menus):
    """Order each named menu by GROUPS, a separator between groups."""
    for name, groups in GROUPS.items():
        menu = menus.get(name)
        if menu is None or sip.isdeleted(menu):
            continue
        actions = [a for a in menu.actions() if not a.isSeparator()]
        by_label = {}
        for action in actions:
            by_label.setdefault(label(action), action)
        placed, ordered = set(), []
        for group in groups:
            members = [by_label[text] for text in group if text in by_label]
            if members:
                ordered.append(members)
                placed.update(id(a) for a in members)
        subs = []
        for title, sub_groups in SUBMENUS.get(name, ()):
            sub = QMenu(title, menu)
            for group in sub_groups:
                members = [by_label[text] for text in group if text in by_label]
                if members and sub.actions():
                    sub.addSeparator()
                sub.addActions(members)
                placed.update(id(a) for a in members)
            if sub.actions():
                subs.append(sub)
        rest = [a for a in actions if id(a) not in placed and label(a) not in LAST]
        last = [a for a in actions if id(a) not in placed and label(a) in LAST]
        if rest:
            ordered.append(rest)
        if subs:
            ordered.append([sub.menuAction() for sub in subs])
        if last:  # Close Window and Quit stay at the very end.
            ordered.append(last)
        for action in menu.actions():
            menu.removeAction(action)
        for index, group in enumerate(ordered):
            if index:
                menu.addSeparator()
            menu.addActions(group)
