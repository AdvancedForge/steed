"""Workspaces: named sets of tabs inside a profile, switched in place.

A profile keeps your sign-ins; workspaces keep what you are working on
(Work, Trip, Taxes) apart within it. Switching puts this window's web tabs
away under the current workspace and opens the other one's, loading each tab
when you first look at it. Nothing is a third kind of profile: cookies and
history stay the profile's. Kept in the profile's workspaces.json.
"""

import json
from pathlib import Path

from browser.utils import save_json

DEFAULT = "Main"
# What agents may do in a workspace: as your settings say, ask about
# everything, or nothing at all.
AGENT_RULES = ("allowed", "ask", "never")
MAX_WORKSPACES = 100
MAX_TABS = 200


def clean_name(name):
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("Give the workspace a name.")
    return name


class Workspaces:
    def __init__(self, profile_dir):
        self.path = Path(profile_dir) / "workspaces.json"
        self.current = DEFAULT
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raw = {}
        spaces = raw.get("spaces") if isinstance(raw, dict) else None
        self.spaces = {}
        for name, value in (spaces or {}).items():
            if not isinstance(name, str) or not isinstance(value, dict):
                continue
            tabs = [
                {
                    "url": str(t.get("url", ""))[:4096],
                    "title": str(t.get("title", ""))[:300],
                }
                for t in value.get("tabs", [])
                if isinstance(t, dict)
                and str(t.get("url", "")).startswith(("http", "file"))
            ][:MAX_TABS]
            agents = value.get("agents", "allowed")
            self.spaces[name[:60]] = {
                "tabs": tabs,
                "agents": agents if agents in AGENT_RULES else "allowed",
            }
        self.spaces.setdefault(DEFAULT, {"tabs": [], "agents": "allowed"})
        listed = raw.get("everywhere") if isinstance(raw, dict) else None
        self.everywhere = [
            str(url)[:4096] for url in (listed or []) if str(url).startswith("http")
        ][:20]
        current = raw.get("current") if isinstance(raw, dict) else None
        self.current = current if current in self.spaces else DEFAULT

    def names(self):
        """Workspace names, the default first then alphabetical."""
        rest = sorted((n for n in self.spaces if n != DEFAULT), key=str.lower)
        return [DEFAULT, *rest]

    def _save(self):
        save_json(
            self.path,
            {
                "version": 1,
                "current": self.current,
                "everywhere": self.everywhere,
                "spaces": self.spaces,
            },
        )

    def create(self, name):
        name = clean_name(name)
        if name in self.spaces:
            raise ValueError("A workspace has that name already.")
        if len(self.spaces) >= MAX_WORKSPACES:
            raise ValueError(f"Keep at most {MAX_WORKSPACES} workspaces.")
        self.spaces[name] = {"tabs": [], "agents": "allowed"}
        self._save()
        return name

    def put_away(self, name, tabs):
        """Remember a workspace's tabs as they are now."""
        self.spaces.setdefault(name, {"tabs": [], "agents": "allowed"})["tabs"] = list(
            tabs
        )[:MAX_TABS]
        self._save()

    def agents(self, name):
        return self.spaces.get(name, {}).get("agents", "allowed")

    def set_agents(self, name, rule):
        if rule not in AGENT_RULES or name not in self.spaces:
            raise ValueError("Choose allowed, ask or never.")
        self.spaces[name]["agents"] = rule
        self._save()

    def toggle_everywhere(self, url):
        """A tab kept open in every workspace (or no longer)."""
        if url in self.everywhere:
            self.everywhere.remove(url)
        elif url.startswith("http"):
            self.everywhere = (self.everywhere + [url])[-20:]
        self._save()
        return url in self.everywhere

    def take(self, name):
        """The tabs to open for a workspace (they are now open, not stored)."""
        space = self.spaces.get(name)
        if space is None:
            raise ValueError("No workspace has that name.")
        tabs, space["tabs"] = space["tabs"], []
        self._save()
        return tabs

    def rename(self, old, new):
        new = clean_name(new)
        if old == DEFAULT:
            raise ValueError("The main workspace keeps its name.")
        if new in self.spaces:
            raise ValueError("A workspace has that name already.")
        self.spaces[new] = self.spaces.pop(old)
        self._save()
        return new

    def delete(self, name):
        if name == DEFAULT:
            raise ValueError("The main workspace cannot be deleted.")
        self.spaces.pop(name, None)
        self._save()
