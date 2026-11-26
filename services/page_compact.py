"""A page snapshot as agents read it by default: one line per target.

page.inspect's full form repeats seventeen field names for every control, so
a busy page's controls came to 80-100k characters. The compact form keeps
everything an agent acts on (each target's id, role, label, link, value,
operations, states, constraints, form, help and error text), leaves out empty
fields and writes each target as one line; on real pages its target list was
3-9 times smaller. Agents ask for detail="full" to get every field as JSON.
"""

from urllib.parse import urlsplit

HOW_TO_READ = (
    'targets: one line each: [target_id] role "label" then, when present: '
    "-> link address, = current value, {operations}, (states and flags), "
    "!constraints, form: method action, -- description, error: message. "
    "Use target_id with page.act, page.navigate, page.submit and the rest."
)


def _short(url, origin):
    if not url:
        return ""
    if origin and url.startswith(origin):
        return url[len(origin) :] or "/"
    return url


def target_line(target, origin=""):
    label = " ".join((target.label or "").split())[:200]
    line = f'[{target.target_id}] {target.role or target.kind} "{label}"'
    if target.href:
        line += f" -> {_short(target.href, origin)}"
    if target.value_text:
        line += f' = "{" ".join(target.value_text.split())[:120]}"'
    if target.operations:
        line += " {" + ",".join(target.operations) + "}"
    flags = [*target.states]
    for name in ("required", "invalid", "read_only"):
        if getattr(target, name):
            flags.append(name)
    if not target.enabled:
        flags.append("disabled")
    if flags:
        line += " (" + ",".join(flags) + ")"
    if target.constraints:
        line += " !" + ",".join(target.constraints)
    if target.form_action:
        method = (target.form_method or "GET").upper()
        line += f" form: {method} {_short(target.form_action, origin)}"
    if target.description:
        line += " -- " + " ".join(target.description.split())[:160]
    if target.error_message:
        line += " error: " + " ".join(target.error_message.split())[:160]
    return line


def outline_line(item):
    text = " ".join((item.text or "").split())[:160]
    if item.kind == "heading":
        return f"{'#' * max(1, item.level)} {text}"
    parts = [item.kind]
    if item.role and item.role != item.kind:
        parts.append(item.role)
    if text:
        parts.append(f'"{text}"')
    if item.columns:
        parts.append("[" + " | ".join(item.columns) + "]")
    if item.count:
        parts.append(f"{item.count} {'rows' if item.kind == 'table' else 'items'}")
    if item.ordered:
        parts.append("ordered")
    if item.expanded is not None:
        parts.append("expanded" if item.expanded else "collapsed")
    return " ".join(parts)


def compact_snapshot(snapshot):
    """A PageSnapshot as a plain record with text target lines."""
    frames = []
    for frame in snapshot.frames:
        origin = frame.origin or "{0.scheme}://{0.netloc}".format(urlsplit(frame.url))
        frames.append(
            {
                "frame_id": frame.frame_id,
                "parent_frame_id": frame.parent_frame_id,
                "url": frame.url,
                "text": frame.text,
                "outline": [outline_line(item) for item in frame.outline],
                "targets": "\n".join(target_line(t, origin) for t in frame.targets),
                "limitations": list(frame.limitations),
                **({"scroll": frame.scroll} if frame.scroll else {}),
            }
        )
    return {
        "snapshot_id": snapshot.snapshot_id,
        "session_id": snapshot.session_id,
        "url": snapshot.url,
        "title": snapshot.title,
        "revision": snapshot.revision,
        "detail": "compact",
        "how_to_read": HOW_TO_READ,
        "frames": frames,
        "limitations": list(snapshot.limitations),
        "guidance": list(snapshot.guidance),
        "source": snapshot.source,
    }
