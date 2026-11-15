"""Link hints: follow links and press buttons from the keyboard.

Ctrl+; labels every link, button and field you can see with a few letters
(home row first); type a label to click it (or focus the field). Ctrl+Shift+;
opens the chosen link in a new background tab instead. Escape, a click or
scrolling cancels. The labels live in a closed shadow root in Steed's own
script world, out of the page's reach.
"""

import json
import secrets

PREFIX = "\x01steedhints "
ALPHABET = "asdfghjklqweruiopzxcvnm"

SCRIPT = r"""
(() => {
  const TOKEN = __TOKEN__, NEW_TAB = __NEW_TAB__, ALPHABET = __ALPHABET__;
  if (window.__steedHints) window.__steedHints();
  const targets = [...document.querySelectorAll(
    "a[href], button, input:not([type=hidden]), select, textarea, summary, label[for], " +
    "[role=button], [role=link], [role=tab], [role=menuitem], [onclick], [tabindex]:not([tabindex='-1'])"
  )].filter((el) => {
    if (el.disabled) return false;
    if (NEW_TAB && !(el.matches("a[href]") && /^https?:/.test(el.href))) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.right < 0 ||
        r.top > innerHeight || r.left > innerWidth) return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || +style.opacity === 0) return false;
    const hit = document.elementFromPoint(
      Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2)),
      Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2)));
    return !hit || el === hit || el.contains(hit) || hit.contains(el);
  }).slice(0, 600);
  if (!targets.length) return 0;
  // Labels: all the same length, as short as the count allows.
  let length = 1;
  while (ALPHABET.length ** length < targets.length) length++;
  const labels = targets.map((_, i) => {
    let label = "", n = i;
    for (let k = 0; k < length; k++) { label = ALPHABET[n % ALPHABET.length] + label; n = Math.floor(n / ALPHABET.length); }
    return label;
  });
  const host = document.createElement("steed-hints");
  host.style.cssText = "position:fixed;inset:0;z-index:2147483647;pointer-events:none;";
  const root = host.attachShadow({mode: "closed"});
  const style = document.createElement("style");
  style.textContent = ".h{position:fixed;font:600 11px/1 system-ui,sans-serif;letter-spacing:.04em;" +
    "padding:3px 4px;border-radius:5px;background:#ffd84d;color:#1b1b1b;box-shadow:0 1px 3px #0006;" +
    "text-transform:uppercase}.h b{color:#9a7600}.h.off{display:none}";
  root.append(style);
  const marks = targets.map((el, i) => {
    const r = el.getBoundingClientRect(), mark = document.createElement("span");
    mark.className = "h";
    mark.style.left = Math.max(0, r.left) + "px";
    mark.style.top = Math.max(0, r.top) + "px";
    mark.textContent = labels[i];
    root.append(mark);
    return mark;
  });
  document.documentElement.append(host);
  let typed = "";
  const done = () => {
    removeEventListener("keydown", key, true);
    removeEventListener("mousedown", done, true);
    removeEventListener("scroll", done, true);
    host.remove();
    window.__steedHints = null;
  };
  const act = (el) => {
    done();
    if (NEW_TAB) {
      console.debug("\u0001steedhints " + JSON.stringify({token: TOKEN, url: el.href}));
    } else if (el.matches("input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]), textarea, select")) {
      el.focus();
    } else {
      el.focus({preventScroll: true});
      el.click();
    }
  };
  const key = (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (event.key === "Escape") { done(); return; }
    if (event.key === "Backspace") typed = typed.slice(0, -1);
    else if (event.key.length === 1 && ALPHABET.includes(event.key.toLowerCase())) typed += event.key.toLowerCase();
    else return;
    let left = 0, only = -1;
    labels.forEach((label, i) => {
      const fits = label.startsWith(typed);
      marks[i].classList.toggle("off", !fits);
      if (fits) {
        left++; only = i;
        marks[i].innerHTML = "<b>" + label.slice(0, typed.length) + "</b>" + label.slice(typed.length);
      }
    });
    if (left === 0) done();
    else if (left === 1 && labels[only] === typed) act(targets[only]);
  };
  addEventListener("keydown", key, true);
  addEventListener("mousedown", done, true);
  addEventListener("scroll", done, true);
  window.__steedHints = done;
  return targets.length;
})()
"""

TOKEN = secrets.token_hex(16)


def script(new_tab=False):
    return (
        SCRIPT.replace("__TOKEN__", json.dumps(TOKEN))
        .replace("__NEW_TAB__", "true" if new_tab else "false")
        .replace("__ALPHABET__", json.dumps(ALPHABET))
    )


def parse(message):
    """The address a hint chose to open in a new tab, or None."""
    try:
        data = json.loads(message[len(PREFIX) :])
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("token") != TOKEN:
        return None
    url = data.get("url")
    return (
        url
        if isinstance(url, str) and url.startswith(("http://", "https://"))
        else None
    )
