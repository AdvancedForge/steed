"""Steed's side of extensions' menus, badges, injected scripts and shortcuts.

Each switched-on extension gets a hidden page of its own, steed-bridge.html
(written by extension_build), which talks to the extension's background
through a port and to Steed through its console (out) and __steedBridge (in).
Through it the stand-ins in extension_shim.js reach Steed:

- contextMenus: items kept here (and in extensions.json, as Chrome keeps them
  across restarts), shown in the page menu; clicks go back to the extension.
- action: badge text, colour and title per tab, shown on pinned buttons.
- scripting: executeScript, insertCSS/removeCSS and registered content
  scripts, run by Steed in the tab, in a script world of the extension's own
  (ISOLATED) or the page's (MAIN). Steed's worlds have no chrome.* APIs.
- commands and action.onClicked: shortcuts and toolbar clicks.

The stand-ins name tabs by ids of their own; Steed matches them to its tabs by
address, then title, then which is showing.
"""

import json
import re
import time
import zlib
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

import webengine

PREFIX = "\x01steed "
SCRIPT_PREFIX = "steed-extension-"
RESULT_POLL_MS = 50
RESULT_TIMEOUT_SECONDS = 30
MAX_ITEMS = 1000
_RUN_AT = {
    "document_start": "start",
    "document_end": "ready",
    "document_idle": "loaded",
}

# Match patterns as Chrome reads them, in JavaScript for registered scripts.
MATCH_JS = r"""
const steedMatch = (pattern, url) => {
  if (pattern === "<all_urls>") return /^(https?|wss?|ftp|file):/.test(url);
  const parts = /^(\*|https?|wss?|ftp|file):\/\/([^/]*)(\/.*)$/.exec(pattern);
  if (!parts) return false;
  const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const scheme = parts[1] === "*" ? "(https?|wss?)" : parts[1];
  const host = parts[2] === "*" ? "[^/]*"
    : parts[2].startsWith("*.") ? "([^/]*\\.)?" + escape(parts[2].slice(2)) : escape(parts[2]);
  return new RegExp("^" + scheme + "://" + host + "(:[0-9]+)?" + escape(parts[3]).replace(/\\\*/g, ".*") + "$").test(url);
};
"""


def world_for(extension_id: str) -> int:
    """A script world of the extension's own for its injected scripts."""
    return 2 + zlib.crc32(extension_id.encode()) % 200


def matches(patterns, url: str) -> bool:
    """Chrome match patterns ("<all_urls>", "*://*.example.com/*")."""
    for pattern in patterns or []:
        if not isinstance(pattern, str):
            continue
        if pattern == "<all_urls>":
            if re.match(r"^(https?|wss?|ftp|file):", url):
                return True
            continue
        parts = re.match(r"^(\*|https?|wss?|ftp|file)://([^/]*)(/.*)$", pattern)
        if not parts:
            continue
        scheme = "(https?|wss?)" if parts.group(1) == "*" else re.escape(parts.group(1))
        host = parts.group(2)
        if host == "*":
            host_re = "[^/]*"
        elif host.startswith("*."):
            host_re = r"([^/]*\.)?" + re.escape(host[2:])
        else:
            host_re = re.escape(host)
        path_re = re.escape(parts.group(3)).replace(r"\*", ".*")
        if re.match(f"^{scheme}://{host_re}(:[0-9]+)?{path_re}$", url):
            return True
    return False


def needs_bridge(manifest: dict) -> bool:
    asked = manifest.get("permissions") or []
    return bool(
        manifest.get("action")
        or manifest.get("commands")
        or "contextMenus" in asked
        or "scripting" in asked
    )


class BridgePage(webengine.Page):
    def __init__(self, profile, received, parent=None):
        super().__init__(profile, parent)
        self.received = received
        self.ready = False
        self.queue = []
        self.loadFinished.connect(self._loaded)

    def _loaded(self, ok):
        self.ready = ok
        if ok:
            for message in self.queue:
                self.send(message)
            self.queue.clear()

    def send(self, message):
        if not self.ready:
            self.queue.append(message)
            return
        webengine.run_script(
            self,
            f"window.__steedBridge && window.__steedBridge({json.dumps(message)})",
            world=webengine.PAGE_WORLD,
        )

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith(PREFIX):
            try:
                self.received(json.loads(message[len(PREFIX) :]))
            except (ValueError, TypeError):
                pass


class Bridges(QObject):
    """Bridge pages and what they carry, for every profile's extensions."""

    menus_changed = pyqtSignal(str)  # profile
    badges_changed = pyqtSignal(str)  # profile

    def __init__(self, extensions):
        super().__init__(extensions)
        self.extensions = extensions
        self.pages = {}  # (profile, extension id) -> BridgePage
        self.badges = {}  # (profile, extension id) -> {tab id or "*": {...}}
        self.tab_lists = {}  # (profile, extension id) -> [tab info]
        self._results = 0

    # -- pages

    def sync(self, name):
        """A bridge page for each switched-on extension that needs one."""
        if not self.extensions.available(name):
            return
        profile = self.extensions.profiles[name][0]
        wanted = {}
        for entry in self.extensions.installed(name):
            manifest = self.manifest(entry)
            if entry["enabled"] and manifest and needs_bridge(manifest):
                wanted[entry["id"]] = entry
        for key in [k for k in self.pages if k[0] == name and k[1] not in wanted]:
            self._drop(key)
        for extension_id in wanted:
            key = (name, extension_id)
            if key in self.pages:
                continue
            page = BridgePage(
                profile, lambda message, k=key: self._received(k, message), self
            )
            self.pages[key] = page
            page.setUrl(QUrl(f"chrome-extension://{extension_id}/steed-bridge.html"))
        self._install_scripts(name)

    def _drop(self, key):
        page = self.pages.pop(key, None)
        if page is not None:
            from PyQt6 import sip

            if not sip.isdeleted(page):
                sip.delete(page)
        self.badges.pop(key, None)
        self.tab_lists.pop(key, None)

    def drop(self, name, extension_id):
        """Before an extension is removed, installed again or switched off:
        Chromium stops the process if one goes while its page is showing."""
        self._drop((name, extension_id))

    def close(self, name=None):
        """Destroy bridge pages (all, or a profile's) before their profiles."""
        for key in [k for k in self.pages if name is None or k[0] == name]:
            self._drop(key)

    def manifest(self, entry) -> dict | None:
        try:
            return json.loads((Path(entry["path"]) / "manifest.json").read_text())
        except (OSError, ValueError, KeyError):
            return None

    def send(self, name, extension_id, message):
        page = self.pages.get((name, extension_id))
        if page is not None:
            page.send(message)

    # -- what the extensions say

    def _received(self, key, message):
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "menu":
            self._menu(key, message)
        elif kind == "badge":
            self._badge(key, message)
        elif kind == "tabs":
            self.tab_lists[key] = [
                t for t in message.get("tabs", []) if isinstance(t, dict)
            ]
            self.badges_changed.emit(key[0])
        elif kind in ("execute", "css"):
            self._inject(key, message)
        elif kind == "register":
            self._register(key, message)

    def _answer(self, key, ask, value=None, error=None):
        message = {"type": "answer", "ask": ask, "value": value}
        if error:
            message["error"] = error
        self.send(key[0], key[1], message)

    # -- menus

    def _state(self, name):
        from extensions import read_state

        return read_state(self.extensions.folder(name))

    def menus(self, name, extension_id) -> list[dict]:
        return self._state(name)["menus"].get(extension_id, [])

    def _menu(self, key, message):
        name, extension_id = key
        state = self._state(name)
        items = state["menus"].get(extension_id, [])
        op, item_id = message.get("op"), message.get("id")
        props = message.get("props") if isinstance(message.get("props"), dict) else {}
        if op == "create" and len(items) < MAX_ITEMS:
            items = [i for i in items if i["id"] != item_id]
            items.append({**props, "id": item_id})
        elif op == "update":
            items = [{**i, **props} if i["id"] == item_id else i for i in items]
        elif op == "remove":
            gone = {item_id}
            # Removing an item removes its children too.
            changed = True
            while changed:
                more = {i["id"] for i in items if i.get("parentId") in gone} - gone
                changed = bool(more)
                gone |= more
            items = [i for i in items if i["id"] not in gone]
        elif op == "removeAll":
            items = []
        state["menus"][extension_id] = items
        from extensions import write_state

        write_state(self.extensions.folder(name), state)
        self.menus_changed.emit(name)

    def click(self, name, extension_id, item, context, browser_tab):
        """A menu item was chosen: tell the extension, as Chrome does."""
        info = {"menuItemId": item["id"], **context}
        if item.get("parentId") is not None:
            info["parentMenuItemId"] = item["parentId"]
        if item.get("type") in ("checkbox", "radio"):
            info["wasChecked"] = bool(item.get("checked"))
            info["checked"] = (
                True if item["type"] == "radio" else not info["wasChecked"]
            )
            self._menu(
                (name, extension_id),
                {
                    "op": "update",
                    "id": item["id"],
                    "props": {"checked": info["checked"]},
                },
            )
        self.send(
            name,
            extension_id,
            {
                "type": "menuClicked",
                "info": info,
                "tabId": self.tab_id(name, extension_id, browser_tab),
            },
        )

    def command(self, name, extension_id, command, browser_tab):
        kind = "actionClicked" if command == "_execute_action" else "command"
        self.send(
            name,
            extension_id,
            {
                "type": kind,
                "name": command,
                "tabId": self.tab_id(name, extension_id, browser_tab),
            },
        )

    # -- badges

    def _badge(self, key, message):
        field = message.get("field")
        if field not in ("text", "color", "title"):
            return
        tab = message.get("tabId")
        slot = "*" if tab is None else tab
        self.badges.setdefault(key, {}).setdefault(slot, {})[field] = str(
            message.get("value", "")
        )[:100]
        self.badges_changed.emit(key[0])

    def badge(self, name, extension_id, browser_tab) -> dict:
        """{text, color, title} for the tab (its own over every tab's)."""
        found = self.badges.get((name, extension_id), {})
        tab = self.tab_id(name, extension_id, browser_tab)
        return {**found.get("*", {}), **found.get(tab, {})}

    # -- tabs: the stand-ins' ids and Steed's tabs

    def windows(self, name):
        from PyQt6.QtWidgets import QApplication

        return [
            window
            for window in QApplication.topLevelWidgets()
            if hasattr(window, "tabs")
            and hasattr(window, "profile_manager")
            and not getattr(window, "_closing", True)
            and window.profile_manager.current_profile_name == name
        ]

    def _showing(self, browser_tab):
        window = getattr(browser_tab, "browser_window", None)
        return bool(
            window is not None
            and window.tabs
            and 0 <= window.current_tab_index < len(window.tabs)
            and window.tabs[window.current_tab_index] is browser_tab
        )

    def _score(self, info, browser_tab):
        if info.get("url") != browser_tab.url().toString():
            return -1
        return (info.get("title") == browser_tab.title()) * 2 + (
            bool(info.get("visible")) == self._showing(browser_tab)
        )

    def tab_id(self, name, extension_id, browser_tab):
        """The stand-ins' id for one of Steed's tabs, or None."""
        if browser_tab is None:
            return None
        best = max(
            self.tab_lists.get((name, extension_id), []),
            key=lambda info: self._score(info, browser_tab),
            default=None,
        )
        if best is None or self._score(best, browser_tab) < 0:
            return None
        return best.get("id")

    def browser_tab(self, name, extension_id, tab_id):
        """Steed's tab for one of the stand-ins' ids, or None."""
        info = next(
            (
                t
                for t in self.tab_lists.get((name, extension_id), [])
                if t.get("id") == tab_id
            ),
            None,
        )
        if info is None:
            return None
        tabs = [tab for window in self.windows(name) for tab in window.tabs]
        best = max(tabs, key=lambda tab: self._score(info, tab), default=None)
        return best if best is not None and self._score(info, best) >= 0 else None

    # -- scripting

    def _source_file(self, name, extension_id, file):
        """A file of the installed extension, read safely."""
        entry = next(
            (e for e in self.extensions.installed(name) if e["id"] == extension_id),
            None,
        )
        if entry is None:
            raise ValueError("The extension is not installed.")
        root = Path(entry["path"]).resolve()
        path = (root / str(file).lstrip("/")).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"Could not load file: '{file}'.")
        return path.read_text("utf-8")

    def _inject(self, key, message):
        name, extension_id = key
        ask = message.get("ask")
        tab = self.browser_tab(name, extension_id, message.get("tabId"))
        if tab is None:
            self._answer(key, ask, error=f"No tab with id: {message.get('tabId')}.")
            return
        world = (
            webengine.PAGE_WORLD
            if message.get("world") == "MAIN"
            else world_for(extension_id)
        )
        try:
            files = [
                self._source_file(name, extension_id, f)
                for f in message.get("files") or []
            ]
        except ValueError as error:
            self._answer(key, ask, error=str(error))
            return
        if message["type"] == "css":
            css = message.get("css") or "\n".join(files)
            mark = json.dumps(f"{extension_id}:{zlib.crc32(css.encode())}")
            if message.get("op") == "remove":
                source = (
                    f"document.querySelectorAll('style[data-steed-css=' + "
                    f"JSON.stringify({mark}) + ']').forEach((s) => s.remove());"
                )
            else:
                source = (
                    "(() => { const style = document.createElement('style');"
                    f" style.dataset.steedCss = {mark};"
                    f" style.textContent = {json.dumps(css)};"
                    " (document.head || document.documentElement).append(style); })();"
                )
            webengine.run_script(tab.page(), source, world=world)
            self._answer(key, ask, None)
            return
        if message.get("func"):
            args = json.dumps(message.get("args") or [])
            self._run_async(key, ask, tab, f"({message['func']})(...{args})", world)
        else:
            webengine.run_script(
                tab.page(),
                ";\n".join(files),
                lambda result: self._answer(
                    key, ask, [{"frameId": 0, "result": result}]
                ),
                world=world,
            )

    def _run_async(self, key, ask, tab, expression, world):
        """Run an expression that may return a promise; answer with its value."""
        self._results += 1
        slot = self._results
        webengine.run_script(
            tab.page(),
            "(() => { const store = (window.__steedResults ||= {});"
            f" try {{ Promise.resolve({expression}).then("
            f"(value) => {{ store[{slot}] = {{ ok: true, value: value ?? null }}; }},"
            f" (error) => {{ store[{slot}] = {{ ok: false, error: String(error && error.message || error) }}; }}); }}"
            f" catch (error) {{ store[{slot}] = {{ ok: false, error: String(error && error.message || error) }}; }} }})();",
            world=world,
        )
        end = time.monotonic() + RESULT_TIMEOUT_SECONDS
        page = tab.page()

        def poll():
            if page is None:
                return
            webengine.run_script(
                page,
                f"(() => {{ const store = window.__steedResults || {{}};"
                f" const found = store[{slot}]; delete store[{slot}]; return found ?? null; }})()",
                got,
                world=world,
            )

        def got(found):
            if isinstance(found, dict):
                if found.get("ok"):
                    self._answer(
                        key, ask, [{"frameId": 0, "result": found.get("value")}]
                    )
                else:
                    self._answer(key, ask, error=found.get("error") or "Script failed.")
            elif time.monotonic() < end:
                QTimer.singleShot(RESULT_POLL_MS, poll)
            else:
                self._answer(key, ask, error="The script did not finish.")

        QTimer.singleShot(0, poll)

    # -- registered content scripts

    def scripts(self, name, extension_id) -> list[dict]:
        return self._state(name)["scripts"].get(extension_id, [])

    def _register(self, key, message):
        name, extension_id = key
        state = self._state(name)
        current = state["scripts"].get(extension_id, [])
        op = message.get("op")
        ask = message.get("ask")
        given = [s for s in message.get("scripts") or [] if isinstance(s, dict)]
        ids = message.get("ids")
        if op == "list":
            listed = [s for s in current if ids is None or s.get("id") in ids]
            self._answer(key, ask, listed)
            return
        if op == "add":
            taken = {s["id"] for s in current}
            if any(s.get("id") in taken or not s.get("id") for s in given):
                self._answer(key, ask, error="Duplicate or missing script id.")
                return
            current = current + given
        elif op == "update":
            updates = {s.get("id"): s for s in given}
            current = [{**s, **updates.get(s["id"], {})} for s in current]
        elif op == "remove":
            current = [s for s in current if ids is not None and s["id"] not in ids]
        state["scripts"][extension_id] = current
        from extensions import write_state

        write_state(self.extensions.folder(name), state)
        self._install_scripts(name)
        self._answer(key, ask, None)

    def _install_scripts(self, name):
        """Registered content scripts of switched-on extensions, in the profile."""
        if not self.extensions.available(name):
            return
        profile = self.extensions.profiles[name][0]
        collection = profile.scripts()
        for script in collection.toList():
            if script.name().startswith(SCRIPT_PREFIX):
                collection.remove(script)
        state = self._state(name)
        on = {e["id"] for e in self.extensions.installed(name) if e["enabled"]}
        for extension_id, scripts in state["scripts"].items():
            if extension_id not in on:
                continue
            for script in scripts:
                try:
                    source = self._script_source(name, extension_id, script)
                except ValueError:
                    continue
                collection.insert(
                    webengine.page_script(
                        f"{SCRIPT_PREFIX}{extension_id}-{script['id']}",
                        source,
                        world=webengine.PAGE_WORLD
                        if script.get("world") == "MAIN"
                        else world_for(extension_id),
                        when=_RUN_AT.get(script.get("runAt"), "loaded"),
                        frames=bool(script.get("allFrames")),
                    )
                )

    def _script_source(self, name, extension_id, script):
        js = [self._source_file(name, extension_id, f) for f in script.get("js") or []]
        css = "\n".join(
            self._source_file(name, extension_id, f) for f in script.get("css") or []
        )
        style = (
            "(() => { const style = document.createElement('style');"
            f" style.textContent = {json.dumps(css)};"
            " (document.head || document.documentElement).append(style); })();"
            if css
            else ""
        )
        return (
            "(() => {"
            + MATCH_JS
            + f"const wanted = {json.dumps(script.get('matches') or [])};"
            + f" const unwanted = {json.dumps(script.get('excludeMatches') or [])};"
            + " if (!wanted.some((p) => steedMatch(p, location.href))"
            + " || unwanted.some((p) => steedMatch(p, location.href))) return;"
            + style
            + "\n"
            + ";\n".join(js)
            + "\n})();"
        )
