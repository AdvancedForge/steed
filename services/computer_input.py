"""Browser-target input through the existing private native frontend."""

from services.agent_errors import AgentRequestError

KEYS = {
    "Enter": {
        "key": "Enter",
        "code": "Enter",
        "windowsVirtualKeyCode": 13,
        "text": "\r",
    },
    "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
    "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    "Backspace": {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
    "Delete": {"key": "Delete", "code": "Delete", "windowsVirtualKeyCode": 46},
    "ArrowLeft": {"key": "ArrowLeft", "code": "ArrowLeft", "windowsVirtualKeyCode": 37},
    "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "windowsVirtualKeyCode": 38},
    "ArrowRight": {
        "key": "ArrowRight",
        "code": "ArrowRight",
        "windowsVirtualKeyCode": 39,
    },
    "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "windowsVirtualKeyCode": 40},
    "Home": {"key": "Home", "code": "Home", "windowsVirtualKeyCode": 36},
    "End": {"key": "End", "code": "End", "windowsVirtualKeyCode": 35},
    "PageUp": {"key": "PageUp", "code": "PageUp", "windowsVirtualKeyCode": 33},
    "PageDown": {"key": "PageDown", "code": "PageDown", "windowsVirtualKeyCode": 34},
    "Space": {"key": " ", "code": "Space", "windowsVirtualKeyCode": 32, "text": " "},
    "SelectAll": {
        "key": "a",
        "code": "KeyA",
        "windowsVirtualKeyCode": 65,
        "modifiers": 2,
    },
}


def arguments(operation, values):
    if operation == "click":
        if set(values) != {"x", "y"} or any(
            type(v) is not int or not 0 <= v < 4096 for v in values.values()
        ):
            raise AgentRequestError(
                "invalid_coordinates",
                "Use integer x/y pixels within the returned screenshot.",
            )
    elif operation == "type":
        if (
            set(values) != {"text"}
            or not isinstance(values["text"], str)
            or not 0 < len(values["text"]) <= 4096
        ):
            raise AgentRequestError(
                "invalid_text",
                "Type between 1 and 4096 characters into the focused page control.",
            )
    elif operation == "key":
        if (
            set(values) != {"key"}
            or not isinstance(values["key"], str)
            or values["key"] not in KEYS
        ):
            raise AgentRequestError(
                "invalid_key", "Choose one key from the published page.key enum."
            )
    else:
        raise AgentRequestError("invalid_computer_action", "Use click, type or key.")
    return dict(values)


SCRIPT = """
(() => {
    const request = /* REQUEST */;
    window[request.id] = null;
    (async () => {
        let target;
        try {
            const SDK = await import(new URL('./core/sdk/sdk.js', location.href).href);
            const manager = SDK.TargetManager.TargetManager.instance();
            target = manager.primaryPageTarget() || manager.targets().find(t => t.type() === 'frame');
            if (!target || window[request.id] === false) throw new Error('Unavailable');
            const metrics = await target.pageAgent().invoke_getLayoutMetrics({});
            const v = metrics.cssVisualViewport, expected = request.viewport;
            if (metrics.getError() || !v) throw new Error('Unavailable');
            if (Object.keys(expected).some(k => Math.abs(v[k] - expected[k]) > 0.1)) {
                window[request.id] = {error:'stale_capture'}; return;
            }
            const focus = await target.emulationAgent().invoke_setFocusEmulationEnabled({enabled:true});
            if (focus.getError() || window[request.id] === false) throw new Error('Unavailable');
            const input = target.inputAgent();
            const check = r => { if (r.getError()) throw new Error('Unavailable'); };
            if (request.operation === 'click') {
                const x = request.values.x * request.css_width / request.image_width + v.offsetX;
                const y = request.values.y * request.css_height / request.image_height + v.offsetY;
                check(await input.invoke_dispatchMouseEvent({type:'mouseMoved', x, y}));
                if (window[request.id] === false) return;
                check(await input.invoke_dispatchMouseEvent({type:'mousePressed', x, y, button:'left', buttons:1, clickCount:1}));
                check(await input.invoke_dispatchMouseEvent({type:'mouseReleased', x, y, button:'left', buttons:0, clickCount:1}));
            } else if (request.operation === 'type') {
                check(await input.invoke_insertText({text:request.values.text}));
            } else {
                check(await input.invoke_dispatchKeyEvent({type:'keyDown', ...request.key}));
                check(await input.invoke_dispatchKeyEvent({type:'keyUp', ...request.key}));
            }
            if (window[request.id] !== false) window[request.id] = true;
        } catch (_) { if (window[request.id] !== false) window[request.id] = false; }
        finally {
            if (target) await target.emulationAgent().invoke_setFocusEmulationEnabled({enabled:false});
        }
    })();
})();
"""


# Before a screenshot-guided click, typing or key: never into a password or
# card field, and never a press that could send one the person filled (in
# the same form or, for a control in no form, a field in no form either).
# Card fields inside payment providers' frames count too. Runs in Steed's own
# script world.
GUARD = r"""
(() => {
    const R = /* REQUEST */;
    const CARD = /(card.?(number|num|no)|cc.?(num|number)|\bcvv|\bcvc|\bcsc\b|security.?code)/i;
    const PAY_FRAME = /stripe|braintree|adyen|checkout|payment|card|paypal|klarna/i;
    const payment = el => ["INPUT", "SELECT"].includes(el.tagName) && (
        (el.getAttribute("autocomplete") || "").toLowerCase().split(/\s+/).some(t => t.startsWith("cc-")) ||
        CARD.test([el.name, el.id, el.placeholder, el.getAttribute("aria-label")].join(" ")));
    const secret = el => el.tagName === "INPUT" && (el.type === "password" ||
        (el.getAttribute("type") || "").toLowerCase() === "password" ||
        !["", "none"].includes(getComputedStyle(el).webkitTextSecurity || ""));
    const payFrame = el => el && el.tagName === "IFRAME" &&
        PAY_FRAME.test([el.src, el.name, el.title, el.id].join(" "));
    const sensitive = el => !!el && (secret(el) || payment(el) || payFrame(el));
    const deepest = (root, pick) => {
        let el = pick(root);
        for (let depth = 0; el?.shadowRoot && depth < 8; depth++) {
            const inner = pick(el.shadowRoot);
            if (!inner || inner === el) break;
            el = inner;
        }
        return el;
    };
    const near = el => {
        if (!el) return false;
        const form = el.form || el.closest?.("form");
        const pending = [form || document];
        for (let seen = 0; pending.length && seen < 5000;) {
            for (const node of pending.shift().querySelectorAll("*")) {
                if (++seen >= 5000) break;
                if (node.shadowRoot) pending.push(node.shadowRoot);
                if (node.getClientRects().length && (form || !node.form) &&
                    ((node.value && sensitive(node)) || payFrame(node)))
                    return true;
            }
        }
        return false;
    };
    const focus = deepest(document, root => root.activeElement);
    if (R.operation === "click") {
        const hit = deepest(document, root => root.elementFromPoint(R.x, R.y));
        return !(sensitive(hit) || near(hit));
    }
    if (R.operation === "type") return !sensitive(focus);
    return !(sensitive(focus) || (["Enter", "Space"].includes(R.key) && near(focus)));
})()
"""
