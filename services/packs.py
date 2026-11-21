"""Packs: your pages, skills and automations shared as one file.

Export picks what goes in; a pack holds exactly what each item is (a page's
HTML, a skill's ask, an automation's trigger and steps, with any web addresses
and text a saved step types) and nothing else: no ids, run history, profiles,
logins or settings. Import reads a pack with the same checks as anything you
make yourself, shows every item, and adds only what you tick. Automations
arrive paused, and one started by a watched page comes in as run-by-hand,
because watches are your own.
"""

import json

KIND = "steed-pack"
VERSION = 1
MAX_BYTES = 2 * 1024 * 1024
SECTIONS = ("pages", "skills", "automations")


def _types_text(steps):
    return any("type" in step for step in steps or ())


def private_text(section, item):
    """Whether an item carries text a saved step types (worth a look before
    sharing: a search, an address, a name)."""
    if section == "skills":
        return _types_text(item.get("steps"))
    if section == "automations":
        return any(_types_text(s.get("steps")) for s in item["steps"])
    return False


def build(registry, profile, *, pages=(), skills=(), automations=(), name=""):
    """A pack of the chosen pages (by name), skills and automations (by id)."""
    from services import automations as automation_rules
    from services.skills import check as check_skill
    from services.start_pages import clean_your_page

    pack: dict = {"kind": KIND, "version": VERSION, "name": str(name).strip()[:80]}
    pack["pages"] = [
        clean_your_page(page)
        for page in registry.start_pages.your_pages(profile)
        if page["name"] in pages
    ]
    pack["skills"] = [
        check_skill(skill) for skill in registry.skills.list() if skill["id"] in skills
    ]
    pack["automations"] = []
    for item in registry.automations.list():
        if item["id"] not in automations:
            continue
        item = automation_rules.check(item)
        if item["trigger"]["kind"] == "watch":
            item["trigger"] = {"kind": "manual"}
            item["was_watch"] = True
        pack["automations"].append(item)
    return pack


def dumps(pack):
    return json.dumps(pack, indent=2, ensure_ascii=False) + "\n"


def read(text):
    """A pack's contents, each item checked: {name, pages, skills, automations,
    problems}. ValueError when it is not a pack at all."""
    from services import automations as automation_rules
    from services.skills import check as check_skill
    from services.start_pages import clean_your_page

    if isinstance(text, bytes):
        if len(text) > MAX_BYTES:
            raise ValueError("That pack is too big.")
        text = text.decode("utf-8", "replace")
    if len(text) > MAX_BYTES:
        raise ValueError("That pack is too big.")
    try:
        raw = json.loads(text)
    except ValueError:
        raise ValueError("That file is not a Steed pack.") from None
    if not isinstance(raw, dict) or raw.get("kind") != KIND:
        raise ValueError("That file is not a Steed pack.")
    if not isinstance(raw.get("version"), int) or raw["version"] > VERSION:
        raise ValueError("That pack is from a newer Steed; update Steed first.")
    found: dict = {"name": " ".join(str(raw.get("name", "")).split())[:80]}
    found["problems"] = []
    checks = {
        "pages": clean_your_page,
        "skills": check_skill,
        "automations": automation_rules.check,
    }
    for section in SECTIONS:
        items = raw.get(section) or []
        if not isinstance(items, list):
            items = []
        found[section] = []
        for number, item in enumerate(items[:100], 1):
            try:
                clean = checks[section](item)
            except ValueError as error:
                found["problems"].append(f"{section[:-1].title()} {number}: {error}")
                continue
            if section == "automations" and item.get("was_watch"):
                clean["was_watch"] = True
            found[section].append(clean)
    if not any(found[s] for s in SECTIONS):
        raise ValueError(
            "Nothing in that pack can be added."
            + (" " + found["problems"][0] if found["problems"] else "")
        )
    return found


def _free_page_name(registry, profile, name):
    taken = {p["name"] for p in registry.start_pages.your_pages(profile)}
    if name not in taken:
        return name
    for number in range(2, 100):
        candidate = f"{name[:28]}-{number}"
        if candidate not in taken:
            return candidate
    raise ValueError("Too many pages with that name.")


def install(registry, profile, found, chosen):
    """Add the chosen items ({section: [index, ...]}); what happened, as
    {added: [text], skipped: [text]}."""
    if profile == "search":
        raise ValueError("Incognito windows cannot add packs.")
    added, skipped = [], []
    renamed_skills = {}
    for index in chosen.get("skills", ()):
        skill = dict(found["skills"][index])
        current = registry.skills.find(skill["name"])
        if current and current["ask"] == skill["ask"]:
            skipped.append(f"Skill “{skill['name']}”: you have it already.")
            continue
        if current:
            original = skill["name"]
            for number in range(2, 100):
                skill["name"] = f"{original[:54]} ({number})"
                if not registry.skills.find(skill["name"]):
                    break
            renamed_skills[original.lower()] = skill["name"]
        try:
            registry.skills.add(skill)
        except ValueError as error:
            skipped.append(f"Skill “{skill['name']}”: {error}")
            continue
        added.append(f"Skill “{skill['name']}”")
    for index in chosen.get("pages", ()):
        page = found["pages"][index]
        try:
            name = registry.start_pages.save_your_page(
                profile,
                _free_page_name(registry, profile, page["name"]),
                page["title"],
                page["html"],
            )
        except ValueError as error:
            skipped.append(f"Page steed:{page['name']}: {error}")
            continue
        added.append(f"Page steed:{name}")
    for index in chosen.get("automations", ()):
        item = {
            k: v for k, v in found["automations"][index].items() if k != "was_watch"
        }
        item["steps"] = [
            dict(step, value=renamed_skills.get(step["value"].lower(), step["value"]))
            if step["do"] == "skill"
            else step
            for step in item["steps"]
        ]
        try:
            saved = registry.automations.add(item, profile)
        except ValueError as error:
            skipped.append(f"Automation “{item['name']}”: {error}")
            continue
        registry.automations.pause(saved["id"])
        added.append(f"Automation “{saved['name']}” (paused)")
    return {"added": added, "skipped": skipped}
