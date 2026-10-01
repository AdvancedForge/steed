// Trusted, fixed inspection code. Runs in Qt's ApplicationWorld, never MainWorld.
(() => {
    "use strict";
    const request = /* REQUEST */;
    const fail = code => JSON.stringify({error: code});
    try {
        if (request.mode === "target") {
            const state = globalThis[request.state_key];
            if (!state || state.snapshot_id !== request.snapshot_id) return fail("stale_snapshot");
            for (const frame of state.frames) {
                frame.dirty ||= frame.observer.takeRecords().length > 0;
                if (frame.dirty || frame.window.document !== frame.document ||
                    frame.document.URL !== frame.url) return fail("stale_snapshot");
            }
            const target = state.targets.get(request.node_id);
            if (!target || !target.node.isConnected) return fail("stale_snapshot");
            return JSON.stringify({valid: true});
        }

        const previous = globalThis[request.state_key];
        if (previous) for (const frame of previous.frames) frame.observer.disconnect();
        const state = {snapshot_id: request.snapshot_id, frames: [], targets: new Map()};
        globalThis[request.state_key] = state;
        const frames = [], limitations = new Set([
            "document_scripts_disabled", "form_values_omitted", "editable_text_omitted",
            "shadow_dom_not_inspected", "canvas_content_not_inspected"
        ]);
        const allowed = new Set(request.origins);
        let remainingText = 32000, remainingTargets = 200, remainingNodes = 12000;

        const clean = (text, limit = 256) => String(text || "").slice(0, limit * 4 + 64)
            .replace(/\s+/g, " ").trim().slice(0, limit);
        const excluded = node => {
            if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
            return /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|INPUT|TEXTAREA|SELECT|OPTION)$/.test(node.tagName) ||
                node.isContentEditable || node.hasAttribute("contenteditable");
        };
        const hidden = (node, win) => {
            if (node.hidden || node.getAttribute("aria-hidden") === "true") return true;
            const style = win.getComputedStyle(node);
            return style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse";
        };
        const readable = (root, win, limit) => {
            const chunks = [];
            let length = 0;
            const walk = node => {
                if (remainingNodes-- <= 0) { limitations.add("node_limit"); return; }
                if (node.nodeType === Node.TEXT_NODE) {
                    if (node.nodeValue.length > limit - length) limitations.add("text_truncated");
                    const text = clean(node.nodeValue, Math.max(0, limit - length));
                    if (text) { chunks.push(text); length += text.length + 1; }
                    return;
                }
                if (node.nodeType !== Node.ELEMENT_NODE || excluded(node) || hidden(node, win)) return;
                for (const child of node.childNodes) {
                    if (length >= limit || remainingNodes <= 0) break;
                    walk(child);
                }
            };
            if (root) walk(root);
            if (length >= limit) limitations.add("text_truncated");
            return chunks.join(" ").slice(0, limit);
        };
        const safeUrl = value => {
            try {
                const url = new URL(value);
                url.username = ""; url.password = "";
                return url.href.slice(0, 2048);
            } catch (_) { return ""; }
        };
        const labelFor = (node, doc, win) => {
            const labelled = (node.getAttribute("aria-labelledby") || "").slice(0, 2048).split(/\s+/).slice(0, 8)
                .map(id => doc.getElementById(id)).filter(Boolean);
            const labelledText = clean(labelled.map(label => readable(label, win, 256)).join(" "));
            if (labelledText) return labelledText;
            const aria = clean(node.getAttribute("aria-label"));
            if (aria) return aria;
            const labels = [];
            if (node.labels) for (let i = 0; i < Math.min(8, node.labels.length); i++) labels.push(node.labels[i]);
            const label = clean(labels.map(item => readable(item, win, 256)).join(" "));
            return label || readable(node, win, 256) || clean(node.getAttribute("title")) ||
                clean(node.getAttribute("placeholder")) || clean(node.getAttribute("name")) || node.tagName.toLowerCase();
        };
        const inspectFrame = (win, parent, path, inheritedOrigin) => {
            if (frames.length >= 16 || path.split("/").length > 8) {
                limitations.add("frame_limit"); return;
            }
            const doc = win.document;
            if (!doc.documentElement || doc.readyState === "loading") throw new Error("not ready");
            const url = doc.URL;
            const origin = url === "about:blank" || url === "about:srcdoc"
                ? inheritedOrigin : new URL(url).origin;
            const frameId = request.snapshot_id + ":frame:" + frames.length;
            const frame = {frame_id: frameId, parent_frame_id: parent, path, url: safeUrl(url),
                origin, text: "", targets: [], limitations: []};
            frames.push(frame);
            if (origin !== "about:blank" && !allowed.has(origin)) {
                frame.limitations.push("frame_origin_out_of_scope"); return;
            }
            const tracked = {window: win, document: doc, url, dirty: false};
            tracked.observer = new win.MutationObserver(() => { tracked.dirty = true; });
            tracked.observer.observe(doc, {subtree: true, childList: true, characterData: true, attributes: true});
            state.frames.push(tracked);
            frame.text = readable(doc.body || doc.documentElement, win, remainingText);
            remainingText = Math.max(0, remainingText - frame.text.length);

            const frameElements = [];
            const walker = doc.createTreeWalker(doc.documentElement, NodeFilter.SHOW_ELEMENT);
            let node = walker.currentNode;
            while (node && remainingNodes-- > 0) {
                // Prune hidden/editable subtrees: their text and descendants are not candidates.
                if (hidden(node, win) || node.hasAttribute("contenteditable")) {
                    node = walker.nextSibling();
                    while (!node && walker.parentNode()) node = walker.nextSibling();
                    continue;
                }
                const tag = node.tagName.toLowerCase(), type = clean(node.getAttribute("type"), 32).toLowerCase();
                if ((tag === "iframe" || tag === "frame") && frameElements.length < 16) frameElements.push(node);
                const role = clean(node.getAttribute("role"), 64).toLowerCase();
                let kind = "", operations = [], href = null;
                const constraints = ["actions_not_implemented"];
                if (tag === "a" && node.hasAttribute("href")) {
                    kind = "link";
                    const link = new URL(node.href, doc.baseURI);
                    if ((link.protocol === "http:" || link.protocol === "https:") && !link.username && !link.password) {
                        href = safeUrl(link.href); operations = ["navigate"];
                    } else constraints.push("unsupported_link_url");
                } else if (tag === "button") { kind = "button"; operations = ["activate"]; }
                else if (tag === "input" && type !== "hidden") {
                    kind = type === "checkbox" || type === "radio" ? type : "input";
                    operations = [kind === "input" ? "enter" : "activate"];
                    if (type === "password") { constraints.push("sensitive_field", "values_omitted"); operations = []; }
                    if (type === "file") { constraints.push("file_actions_unavailable"); operations = []; }
                    if (type === "submit" || type === "button") { kind = "button"; operations = ["activate"]; }
                } else if (tag === "textarea") { kind = "textarea"; operations = ["enter"]; }
                else if (tag === "select") { kind = "select"; operations = ["select"]; }
                else if (["button", "link", "textbox", "combobox", "checkbox", "radio"].includes(role)) {
                    kind = "custom"; constraints.push("custom_control_unsupported");
                }
                if (kind) {
                    if (remainingTargets <= 0) limitations.add("targets_truncated");
                    else {
                        const enabled = !node.matches(":disabled") && node.getAttribute("aria-disabled") !== "true";
                        const readOnly = node.hasAttribute("readonly") || node.getAttribute("aria-readonly") === "true";
                        if (!enabled) constraints.push("disabled");
                        if (readOnly) constraints.push("read_only");
                        const nodeId = "node:" + state.targets.size;
                        state.targets.set(nodeId, {node});
                        frame.targets.push({node_id: nodeId, kind, role: role || kind,
                            label: labelFor(node, doc, win), enabled, read_only: readOnly, href,
                            operations, constraints});
                        remainingTargets--;
                    }
                }
                node = walker.nextNode();
            }
            if (remainingNodes <= 0) limitations.add("node_limit");

            let index = 0;
            for (const element of frameElements) {
                if (index >= 16 || frames.length >= 16) { limitations.add("frame_limit"); break; }
                const childPath = path + "/" + index++;
                if (hidden(element, win)) continue;
                try { inspectFrame(element.contentWindow, frameId, childPath, origin); }
                catch (_) {
                    frames.push({frame_id: request.snapshot_id + ":frame:" + frames.length,
                        parent_frame_id: frameId, path: childPath, url: safeUrl(element.src), origin: "",
                        text: "", targets: [], limitations: ["frame_inaccessible_or_not_ready"]});
                }
            }
        };
        inspectFrame(window, null, "main", "about:blank");
        return JSON.stringify({snapshot_id: request.snapshot_id, title: clean(document.title, 512),
            frames, limitations: [...limitations]});
    } catch (_) { return fail(request.mode === "target" ? "stale_snapshot" : "inspection_unavailable"); }
})();
