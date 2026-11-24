"""Address and payment-card forms: recognise their fields, remember what a
person submits, and fill saved entries when the person asks.

The scripts run in Steed's isolated world, like the password scripts. Field
values never pass through the page's own JavaScript, a card's security code is
never read or filled, and agents never fill card fields (see page_inspection.js).
"""

import json

# What each field is, from its autocomplete token first and its wording second.
_CLASSIFY = r"""
    const TOKENS = {
        'name': 'name', 'given-name': 'given', 'additional-name': 'middle',
        'family-name': 'family', 'organization': 'organization',
        'street-address': 'street', 'address-line1': 'line1',
        'address-line2': 'line2', 'address-line3': 'line2',
        'address-level2': 'city', 'address-level1': 'region',
        'postal-code': 'postal', 'country': 'country', 'country-name': 'country',
        'tel': 'phone', 'tel-national': 'phone', 'email': 'email',
        'cc-name': 'cc-name', 'cc-given-name': 'cc-given',
        'cc-family-name': 'cc-family', 'cc-number': 'cc-number',
        'cc-exp': 'cc-exp', 'cc-exp-month': 'cc-month', 'cc-exp-year': 'cc-year',
        'cc-csc': 'cc-csc',
    };
    const GUESS = [
        ['cc-csc', /\b(cvv|cvc|csc|cid)\b|security.?code|card.?(code|verif)/i],
        ['cc-number', /card.?(number|num|no)\b|cc.?(num|number)|cardnumber|credit.?card/i],
        // One field for both ("MM / YY") before a month or year alone.
        ['cc-exp', /mm\s*\/\s*yy|exp\w*.?date|valid.?(thru|until)/i],
        ['cc-month', /(exp|expir|valid).{0,12}(month|\bmm\b)|cc.?month/i],
        ['cc-year', /(exp|expir|valid).{0,12}(year|\byy)|cc.?year/i],
        ['cc-exp', /expir/i],
        ['cc-name', /name.?on.?card|card.?holder|cardholder|cc.?name/i],
        ['email', /e.?mail/i],
        ['phone', /phone|mobile|\btel\b|telephone/i],
        ['given', /first.?name|given.?name|\bfname\b|forename/i],
        ['family', /last.?name|family.?name|surname|\blname\b/i],
        ['organization', /company|organi[sz]ation|business.?name/i],
        ['line2', /address.?(line)?.?2|apartment|\bapt\b|suite|\bunit\b|flat/i],
        ['line1', /address.?(line)?.?1|street|address/i],
        ['city', /\bcity\b|town|locality|suburb/i],
        ['region', /\bstate\b|province|region|county/i],
        ['postal', /\bzip|postal|post.?code|postcode/i],
        ['country', /country/i],
        ['name', /full.?name|your.?name|^\s*name\s*$|\bname\b/i],
    ];
    const ADDRESS = new Set(['name', 'given', 'middle', 'family', 'organization',
        'street', 'line1', 'line2', 'city', 'region', 'postal', 'country',
        'phone', 'email']);
    const TEXTUAL = ['text', 'email', 'tel', 'search', 'number', ''];
    const fillable = el =>
        (el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement ||
         (el instanceof HTMLInputElement && TEXTUAL.includes(el.type))) &&
        !el.disabled && !el.readOnly;
    // Genuinely visible: not transparent, tiny or placed off the page.
    const visible = el => {
        const box = el.getBoundingClientRect(), style = getComputedStyle(el);
        return box.width >= 8 && box.height >= 8 && style.visibility !== 'hidden' &&
            Number(style.opacity) > 0.1 && box.right > 0 && box.bottom > 0 &&
            box.left < document.documentElement.scrollWidth &&
            box.top < document.documentElement.scrollHeight;
    };
    const deepFields = root => {
        const found = [], pending = [root];
        let seen = 0;
        while (pending.length && seen < 20000) {
            const node = pending.shift();
            node.querySelectorAll('input, select, textarea').forEach(el => found.push(el));
            for (const el of node.querySelectorAll('*')) {
                if (++seen >= 20000) break;
                if (el.shadowRoot) pending.push(el.shadowRoot);
            }
        }
        return found;
    };
    const kindOf = el => {
        const tokens = (el.getAttribute('autocomplete') || '').toLowerCase().split(/\s+/);
        for (const token of tokens.reverse())
            if (TOKENS[token]) return TOKENS[token];
        const labels = el.labels ? [...el.labels].map(l => l.innerText) : [];
        const words = [el.name, el.id, el.placeholder, el.getAttribute('aria-label'),
            ...labels].filter(Boolean).join(' ').slice(0, 400);
        if (!words) return null;
        for (const [kind, pattern] of GUESS)
            if (pattern.test(words)) return kind;
        return null;
    };
    const group = kind => kind && (kind.startsWith('cc-') ? 'cards' : 'addresses');
    const classified = root => deepFields(root)
        .filter(el => fillable(el) && visible(el))
        .map(el => ({el, kind: kindOf(el)}))
        .filter(item => item.kind);
"""


def _script(body, **arguments):
    """Arguments as one JSON object, never spliced into code; nothing runs
    unless the isolated world's own location is still the expected site."""
    return (
        "(() => { const ARGS = "
        + json.dumps(arguments)
        + "; if (location.origin !== ARGS.expected) return null;"
        + _CLASSIFY
        + body
        + "})()"
    )


# Remembers the address or card a form is about to submit, in the isolated
# world, until Steed collects it. Installed in every frame of a profile.
CAPTURE_SCRIPT = (
    "(() => { if (window.__steedFormWatch) return; window.__steedFormWatch = true;"
    + _CLASSIFY
    + r"""
    const actual = e => (e.composedPath && e.composedPath()[0]) || e.target;
    const value = el => el instanceof HTMLSelectElement
        ? (el.selectedIndex > 0 ? (el.options[el.selectedIndex].text || el.value) : '')
        : String(el.value || '');
    const record = root => {
        const scope = root && root.querySelectorAll ? root : document;
        const found = {};
        for (const {el, kind} of classified(scope)) {
            const text = value(el).trim().slice(0, 4096);
            if (text && kind !== 'cc-csc' && !(kind in found)) found[kind] = text;
        }
        const address = {
            name: found.name || [found.given, found.middle, found.family]
                .filter(Boolean).join(' '),
            organization: found.organization || '',
            street: found.street || [found.line1, found.line2].filter(Boolean).join('\n'),
            city: found.city || '', region: found.region || '',
            postal: found.postal || '', country: found.country || '',
            phone: found.phone || '', email: found.email || '',
        };
        let month = found['cc-month'] || '', year = found['cc-year'] || '';
        const exp = /(\d{1,2})\s*[\/\-]\s*(\d{2,4})/.exec(found['cc-exp'] || '');
        if (exp) { month = exp[1]; year = exp[2]; }
        const card = found['cc-number'] ? {
            number: found['cc-number'],
            name: found['cc-name'] || [found['cc-given'], found['cc-family']]
                .filter(Boolean).join(' '),
            exp_month: month.replace(/\D/g, ''), exp_year: year.replace(/\D/g, ''),
        } : null;
        // A street and a place: a whole address, not a newsletter sign-up.
        const whole = address.street && (address.city || address.postal);
        if (whole || card)
            window.__steedFormCapture = {address: whole ? address : null, card};
    };
    // Only the person's own actions count: pages can fire synthetic events.
    document.addEventListener('submit', e => {
        if (e.isTrusted && navigator.userActivation && navigator.userActivation.isActive)
            record(actual(e));
    }, true);
    document.addEventListener('click', e => {
        if (!e.isTrusted) return;
        const target = actual(e);
        const button = target.closest &&
            target.closest('button, input[type=submit], input[type=button], [role=button]');
        if (button) record(button.form || document);
    }, true);
    document.addEventListener('keydown', e => {
        const target = actual(e);
        if (e.isTrusted && e.key === 'Enter' && target instanceof HTMLInputElement)
            record(target.form || document);
    }, true);
})();
"""
)


def take_script(expected, force):
    """Hand over a captured address or card once its form is gone (or the
    page is leaving)."""
    return _script(
        r"""
    const capture = window.__steedFormCapture;
    if (!capture) return null;
    const mark = capture.card ? capture.card.number :
        capture.address.street.split('\n')[0];
    const present = classified(document).some(({el}) => el.value === mark);
    if (present && !ARGS.force) return {pending: true};
    window.__steedFormCapture = null;
    return capture;
""",
        expected=expected,
        force=bool(force),
    )


def detect_script(expected):
    """How many visible address and card fields this frame shows."""
    return _script(
        r"""
    const counts = {addresses: 0, cards: 0, card_number: false};
    for (const {kind} of classified(document)) {
        if (kind === 'cc-csc') continue;
        counts[group(kind)] += 1;
        if (kind === 'cc-number') counts.card_number = true;
    }
    return counts;
""",
        expected=expected,
    )


def fill_script(expected, kind, values):
    """Fill this frame's empty address (or card) fields with one saved entry;
    returns how many fields were filled."""
    return _script(
        r"""
    const v = ARGS.values;
    const words = (v.name || '').trim().split(/\s+/);
    const lines = (v.street || '').split('\n').map(s => s.trim()).filter(Boolean);
    const yy = (v.exp_year || '').slice(-2);
    const wanted = ARGS.kind === 'cards' ? {
        'cc-name': v.name, 'cc-given': words[0] || '',
        'cc-family': words.slice(1).join(' '), 'cc-number': v.number,
        'cc-month': v.exp_month, 'cc-year': v.exp_year,
        'cc-exp': v.exp_month && v.exp_year ? v.exp_month + '/' + yy : '',
    } : {
        name: v.name, given: words[0] || '', family: words.slice(1).join(' '),
        organization: v.organization, street: lines.join(', '),
        line1: lines[0] || '', line2: lines.slice(1).join(', '), city: v.city,
        region: v.region, postal: v.postal, country: v.country, phone: v.phone,
        email: v.email,
    };
    const fields = classified(document).filter(({kind}) => group(kind) === ARGS.kind);
    // With two forms on a page (shipping and billing), fill the one in use.
    const focused = document.activeElement;
    const form = focused && focused.form && fields.some(f => f.el.form === focused.form)
        ? focused.form : null;
    const matches = (option, text) => {
        const want = String(text).trim().toLowerCase();
        const have = [option.value, option.text].map(s => String(s).trim().toLowerCase());
        if (have.includes(want)) return true;
        if (/^\d+$/.test(want)) return have.some(s => /^\d+$/.test(s) &&
            (Number(s) === Number(want) || s === want.slice(-2)));
        return want.length > 3 && have.some(s => s.startsWith(want));
    };
    const native = (el, value) => {
        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    };
    let filled = 0;
    for (const {el, kind} of fields) {
        if (form && el.form !== form) continue;
        let text = wanted[kind];
        if (!text || kind === 'cc-csc') continue;
        if (el instanceof HTMLSelectElement) {
            if (el.selectedIndex > 0) continue;
            const index = [...el.options].findIndex(o => matches(o, text));
            if (index < 0) continue;
            el.selectedIndex = index;
        } else {
            if (el.value) continue;
            if (el instanceof HTMLTextAreaElement && kind === 'street') text = lines.join('\n');
            if (kind === 'cc-exp' && (el.maxLength >= 7 || /yyyy/i.test(el.placeholder)))
                text = v.exp_month + '/' + v.exp_year;
            if (el.maxLength > 0 && text.length > el.maxLength &&
                kind === 'cc-year') text = yy;
            el.focus();
            native(el, text);
            el.dispatchEvent(new Event('input', {bubbles: true}));
        }
        el.dispatchEvent(new Event('change', {bubbles: true}));
        filled += 1;
    }
    return filled;
""",
        expected=expected,
        kind=kind,
        values=values,
    )
