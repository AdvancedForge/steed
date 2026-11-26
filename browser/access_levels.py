"""Access levels: per-capability policies for agents and local pages.

Each capability is set to "never", "ask" or "always". The presets Nothing,
Ask and Everything set every capability in an area at once; any later single
change reports the area as "custom". Policies are stored in config.json.
"""

POLICIES = ("never", "ask", "always")
POLICY_LABELS = {"never": "Never", "ask": "Ask", "always": "Always"}
PRESETS = {"nothing": "never", "ask": "ask", "everything": "always"}
PRESET_LABELS = {
    "nothing": "Nothing",
    "ask": "Ask",
    "everything": "Everything",
    "custom": "Custom",
}

AGENT_CAPABILITIES = {
    "browse": {
        "label": "Read and browse pages",
        "help": "Open approved sites, read their text and structure, scroll and "
        "watch the page.",
        "actions": {"read", "navigate", "inspect", "scroll", "attach"},
    },
    "interact": {
        "label": "Type, click and submit",
        "help": "Fill fields, press buttons, submit forms and use sites with "
        "their own scripts running (live pages). Can change data on sites.",
        "actions": {"edit", "submit", "live"},
    },
    "screen": {
        "label": "Screenshots and mouse/keyboard",
        "help": "See screenshots of pages and click or type anywhere on them.",
        "actions": {"visual", "computer"},
    },
    "files": {
        "label": "Files and downloads",
        "help": "Download and upload files and keep drafts and files for the task.",
        "actions": {"download", "upload", "artifact", "workspace"},
    },
    "code": {
        "label": "Run generated code",
        "help": "Run code the agent writes in Steed's isolated local worker.",
        "actions": {"worker"},
    },
    "apps": {
        "label": "Connected apps",
        "help": "Use app connections you set up, such as reading or sending mail.",
        "actions": {"connector", "app"},
    },
    "signin": {
        "label": "Sign in with your saved passwords",
        "help": "Steed fills and submits a saved login (and its one-time code) "
        "on the site's own sign-in page. Your agent never sees the password, "
        "the code or the username.",
        "actions": set(),
    },
    "reading": {
        "label": "Search what you've read",
        "help": "Search the text of pages you read (with Remember what I read "
        "on) and get the matching pages' text.",
        "actions": set(),
    },
    "customize": {
        "label": "See and change your customizations",
        "help": "Read your own pages, automations, skills, watches, user scripts "
        "and panel apps, and propose changes to them or to your settings. You "
        "review every change before it happens.",
        "actions": set(),
    },
    "profiles": {
        "label": "Your signed-in profiles",
        "help": "Work in your own profiles, with their cookies and sign-ins, "
        "instead of a private throwaway profile.",
        "actions": set(),
    },
}

PAGE_CAPABILITIES = {
    "web": {
        "label": "Load data from the web",
        "help": "Fetch web addresses (weather, news, feeds, APIs) for widgets. "
        "Requests carry no cookies.",
    },
    "tabs": {
        "label": "See open tabs",
        "help": "List the titles and addresses of this profile's open tabs.",
    },
    "history": {
        "label": "See browsing history",
        "help": "Read this profile's recent history.",
    },
    "bookmarks": {
        "label": "See bookmarks",
        "help": "Read this profile's bookmarks.",
    },
    "steed": {
        "label": "See your watches, automations and skills",
        "help": "Read the pages you watch (with what last changed), your "
        "automations and your skills, for dashboards.",
    },
    "automation": {
        "label": "Run your skills and automations, open tabs and notify you",
        "help": "Start a skill or an automation you made, open a web page in a new "
        "tab, or show a desktop notification. Anything an agent then does "
        "still asks you as usual.",
    },
}

AREAS = {
    "agents": ("agent_access", AGENT_CAPABILITIES, "ask"),
    "pages": ("page_access", PAGE_CAPABILITIES, "never"),
}


def _area(area):
    if area not in AREAS:
        raise ValueError("Unknown access area.")
    return AREAS[area]


def values(config, area):
    """Effective policy per capability; invalid stored values fall back."""
    key, capabilities, default = _area(area)
    stored = config.get(key, {})
    stored = stored if isinstance(stored, dict) else {}
    return {
        name: stored.get(name) if stored.get(name) in POLICIES else default
        for name in capabilities
    }


def preset_of(config, area):
    current = set(values(config, area).values())
    for name, policy in PRESETS.items():
        if current == {policy}:
            return name
    return "custom"


def apply_preset(config, area, preset):
    if preset not in PRESETS:
        raise ValueError("Choose Nothing, Ask or Everything.")
    key, capabilities, _ = _area(area)
    config.set(key, {name: PRESETS[preset] for name in capabilities})


def set_policy(config, area, capability, policy):
    key, capabilities, _ = _area(area)
    if capability not in capabilities:
        raise ValueError("Unknown capability.")
    if policy not in POLICIES:
        raise ValueError("Choose Never, Ask or Always.")
    config.set(key, {**values(config, area), capability: policy})


def agent_decision(config, actions, *, named_profile):
    """ "deny", "approve" or "ask" for an agent's requested actions."""
    policies = values(config, "agents")
    needed = {
        name
        for name, spec in AGENT_CAPABILITIES.items()
        if spec["actions"] & set(actions)
    }
    if named_profile:
        needed.add("profiles")
    if not needed:
        return "ask"
    chosen = {policies[name] for name in needed}
    if "never" in chosen:
        return "deny"
    if chosen == {"always"}:
        return "approve"
    return "ask"


def blocked_capabilities(config, actions, *, named_profile):
    """Labels of the capabilities set to Never that a request needs."""
    policies = values(config, "agents")
    names = [
        name
        for name, spec in AGENT_CAPABILITIES.items()
        if spec["actions"] & set(actions) and policies[name] == "never"
    ]
    if named_profile and policies["profiles"] == "never":
        names.append("profiles")
    return [AGENT_CAPABILITIES[name]["label"] for name in names]
