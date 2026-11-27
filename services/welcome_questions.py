"""Welcome's three quick questions: how often you use AI, more features or a
lighter Steed, and how you like notifications. Each is optional, each answer
sets a few defaults (said under the question, so nothing is hidden), and all
of them can be changed later in Settings. The answers are kept in
config.json as welcome_answers.
"""

QUESTIONS = (
    (
        "ai",
        "How often do you use AI?",
        (
            ("daily", "Every day"),
            ("sometimes", "Now and then"),
            ("rarely", "Rarely or never"),
        ),
    ),
    (
        "weight",
        "More features, or lighter on memory?",
        (
            ("full", "Everything on"),
            ("balanced", "Balanced"),
            ("light", "Light on memory"),
        ),
    ),
    (
        "notify",
        "How should Steed tell you things?",
        (
            ("desktop", "On my desktop"),
            ("steed", "Only inside Steed"),
            ("needs_me", "Only when it needs me"),
            ("off", "Not at all"),
        ),
    ),
)

# question -> answer -> (settings, New Tab errands, Agents menu first,
# memory mode, what the page says it set).
EFFECTS = {
    "ai": {
        "daily": (
            {"agent_notifications": True},
            True,
            True,
            None,
            "The Agents menu comes first, New Tab offers errands for your "
            "assistant, and agents tell you when they reply.",
        ),
        "sometimes": (
            {"agent_notifications": True},
            True,
            False,
            None,
            "Agents stay in their menu, New Tab offers a few errands, and agents "
            "tell you when they reply.",
        ),
        "rarely": (
            {"agent_notifications": False},
            False,
            False,
            None,
            "No errands on New Tab and no agent notifications. Steed works fully "
            "without an assistant.",
        ),
    },
    "weight": {
        "full": (
            {"visual_effects": "full", "tab_previews": True},
            None,
            None,
            "performance",
            "Tabs stay loaded and every effect is on (Memory: Performance).",
        ),
        "balanced": (
            {"visual_effects": "full", "tab_previews": True},
            None,
            None,
            "balanced",
            "Tabs you haven't used for 4 hours are unloaded; effects stay on "
            "(Memory: Balanced).",
        ),
        "light": (
            {"visual_effects": "minimal", "tab_previews": False},
            None,
            None,
            "saver",
            "Background tabs pause after 2 minutes and unload after 15, with "
            "fewer effects and no tab previews (Memory: Memory saver).",
        ),
    },
    "notify": {
        "desktop": (
            {"notifications_where": "desktop", "notifications_often": "every"},
            None,
            None,
            None,
            "Notifications on your desktop, every time.",
        ),
        "steed": (
            {"notifications_where": "steed", "notifications_often": "every"},
            None,
            None,
            None,
            "A note in the corner of the window instead of the desktop.",
        ),
        "needs_me": (
            {"notifications_where": "desktop", "notifications_often": "needs_me"},
            None,
            None,
            None,
            "Only approvals and pages that need you, on your desktop.",
        ),
        "off": (
            {"notifications_where": "off", "notifications_often": "every"},
            None,
            None,
            None,
            "No notifications. Steed's own bars still show what needs you.",
        ),
    },
}


def answers(config):
    """The answers given so far: question -> answer."""
    found = config.get("welcome_answers", {})
    if not isinstance(found, dict):
        return {}
    return {q: a for q, a in found.items() if a in EFFECTS.get(q, {})}


def effect(question, answer):
    """(settings, errands, agents first, memory mode, said), or ValueError."""
    try:
        return EFFECTS[question][answer]
    except KeyError:
        raise ValueError("Not one of Welcome's answers.") from None


def menu_order(order, agents_first):
    """The menu order with Agents first, or back at the end."""
    rest = [name for name in order if name != "agents"]
    return ["agents", *rest] if agents_first else [*rest, "agents"]


def apply(config, question, answer):
    """Record the answer and set its settings; returns (errands, agents
    first, memory mode) for the window to apply."""
    settings, errands, agents_first, memory, _ = effect(question, answer)
    for key, value in settings.items():
        config.set(key, value)
    if memory is not None:
        from browser import memory_modes

        memory_modes.apply_mode(config, memory)
    config.set("welcome_answers", {**answers(config), question: answer})
    return errands, agents_first, memory
