"""How the browser's menus read: related items together, an icon for the
ones people look for, and labels written one way.

Menus are built where their actions live (browser_window); this only orders
them. Anything not named here keeps its place at the end, before Exit.
"""

from PyQt6 import sip

GROUPS = {
    "general": (
        ("Settings…", "Customize New Tab…", "Layout Settings…"),
        ("Passwords…", "Fill Login", "Downloads…", "Dictation", "Extensions"),
        (
            "Import from Another Browser…",
            "Sync…",
            "Search Keywords…",
            "User Scripts & Styles…",
            "Ad Blocking…",
            "Website Permissions…",
        ),
        (
            "Clear Browsing Data…",
            "Welcome to Steed",
            "Crash Log…",
            "Import Layout…",
            "Export Layout…",
        ),
    ),
    "tabs": (
        ("New Tab", "Restore Closed Tab", "Reopen Closed Window", "Close Tab"),
        ("Back", "Forward", "Reload", "Home"),
        ("Next Tab", "Previous Tab"),
        ("Tab Groups", "Saved Groups", "Save All Tabs as Group…", "Session History…"),
        ("Bookmark This Page…", "Manage Bookmarks…"),
        ("Organize Tabs with Agent…", "Compare Tabs with Agent…"),
    ),
    "view": (
        (
            "Find in Page…",
            "Find Next",
            "Find Previous",
            "Go to Address Bar",
            "Command Palette…",
            "Search Tabs…",
            "Search History…",
        ),
        ("Zoom In", "Zoom Out", "Reset Zoom (100%)"),
        (
            "Reader View",
            "Translate Page",
            "Save to Reading List",
            "Reading List…",
            "Play or Pause Media",
            "Mini Player",
        ),
        ("Save Page As…", "Take Screenshot", "Close Split View", "App Panel"),
        (
            "Page Style…",
            "Liquid Glass",
            "Compact Mode",
            "Show Top Menu Bar",
            "Show Bottom Status Bar",
            "Reset Page Style…",
            "Reset Layout",
            "Recover Previous Layout",
        ),
        ("Memory & Performance…", "Engine & Features…", "Developer Tools"),
    ),
    "agents": (
        ("Agent Sidebar", "Agent Chat Tab", "Data Panels", "Watches…", "Website Chats"),
        (
            "Access & Permissions…",
            "Task Permissions…",
            "App Connections…",
            "Agent File Library…",
        ),
        ("Enable Local Agent Access", "Agent Connection Setup…"),
    ),
}
LAST = ("Exit",)

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
    "Exit": "exit",
    "Find in Page…": "search",
    "Command Palette…": "command",
    "Zoom In": "zoom-in",
    "Zoom Out": "zoom-out",
    "Reader View": "book",
    "Translate Page": "translate",
    "Take Screenshot": "camera",
    "Save Page As…": "save",
    "Page Style…": "brush",
    "Developer Tools": "code",
    "Agent Sidebar": "spark",
    "Agent Chat Tab": "chat",
    "Watches…": "eye",
    "Website Chats": "chat",
    "Access & Permissions…": "lock",
    "New Profile…": "user",
    "Switch Profile…": "users",
    "Browsing History…": "clock",
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
        rest = [a for a in actions if id(a) not in placed and label(a) not in LAST]
        last = [a for a in actions if id(a) not in placed and label(a) in LAST]
        ordered += [group for group in (rest, last) if group]
        for action in menu.actions():
            menu.removeAction(action)
        for index, group in enumerate(ordered):
            if index:
                menu.addSeparator()
            menu.addActions(group)
