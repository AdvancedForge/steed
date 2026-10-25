// Trusted, fixed inspection code. Runs in Qt's ApplicationWorld, never MainWorld.
(() => {
    "use strict";
    const request = /* REQUEST */;
    const fail = code => JSON.stringify({error: code});
    const liveFingerprint = node => {
        const attributes = el => Array.from(el.attributes, a => [a.name, a.value]);
        const parent = el => el.parentElement || el.getRootNode()?.host;
        let remainingBytes = 65536;
        const context = el => {
            if (!el) return "";
            const content = el.outerHTML;
            remainingBytes -= content.length;
            if (remainingBytes < 0) return null;
            const ancestors = [];
            for (let ancestor = parent(el); ancestor; ancestor = parent(ancestor)) {
                if (ancestors.length >= 128) return null;
                const values = attributes(ancestor);
                remainingBytes -= JSON.stringify(values).length;
                if (remainingBytes < 0) return null;
                ancestors.push(values);
            }
            return [content, ancestors];
        };
        const own = context(node);
        if (!own) return null;
        if ((node.labels?.length || 0) > 8) return null;
        const labels = Array.from(node.labels || [], context);
        const referenced = attribute => (node.getAttribute(attribute) || "").slice(0, 2048).split(/\s+/).filter(Boolean).slice(0, 8)
            .map(id => context(node.getRootNode().getElementById(id)));
        const references = [referenced("aria-labelledby"), referenced("aria-describedby"), referenced("aria-errormessage")];
        if ([...labels, ...references.flat()].some(value => value === null)) return null;
        const encoded = JSON.stringify([own, node.baseURI, labels, references]);
        return encoded.length <= 65536 ? encoded : null;
    };
    const submission = node => {
        const doc = node.ownerDocument, win = doc.defaultView, form = node.form;
        if (!form || win !== window || node.type !== "submit" ||
            !["BUTTON", "INPUT"].includes(node.tagName)) return {error: "form_unsupported"};
        if (node.matches(":disabled") || node.getAttribute("aria-disabled") === "true" ||
            !node.getClientRects().length || win.getComputedStyle(node).visibility !== "visible")
            return {error: "target_not_editable"};
        // Named form controls can shadow action/method/elements/checkValidity.
        const native = property => Object.getOwnPropertyDescriptor(win.HTMLFormElement.prototype, property).get.call(form);
        const override = (attribute, property, fallback) => node.hasAttribute(attribute) ? node[property] : fallback;
        const action = new URL(override("formaction", "formAction", native("action")) || doc.URL, doc.baseURI);
        const method = override("formmethod", "formMethod", native("method")).toUpperCase();
        const enctype = override("formenctype", "formEnctype", native("enctype"));
        const target = override("formtarget", "formTarget", native("target") || doc.querySelector("base[target]")?.target || "");
        if (!["http:", "https:"].includes(action.protocol) || action.username || action.password || action.href.length > 2048)
            return {error: "form_destination_unsupported"};
        const hasFiles = Array.from(native("elements")).some(c => c.type === "file");
        const multipart = hasFiles && request.upload_available && method === "POST" && enctype === "multipart/form-data";
        if (!["GET", "POST"].includes(method) || (!multipart && enctype !== "application/x-www-form-urlencoded") ||
            !["", "_self"].includes(target)) return {error: "form_unsupported"};
        const elements = native("elements");
        if (elements.length > 200) return {error: "form_control_limit"};
        const uploads = [];
        let fileCount = 0;
        for (const control of elements) {
            if (control.type === "password") return {error: "sensitive_form_unsupported"};
            if (control.type === "file") {
                if (!multipart || ++fileCount > 1 || control.multiple || control.webkitdirectory)
                    return {error: "sensitive_form_unsupported"};
                if (!control.disabled && control.files.length) {
                    const binding = globalThis.__steedUploadBindings?.get(control);
                    if (control.files.length !== 1 || !binding || binding.file !== control.files[0])
                        return {error: "upload_not_owned"};
                    uploads.push({upload_id:binding.id, name:binding.file.name, size:binding.file.size});
                }
            }
        }
        return {url: action.href, method, file_form:hasFiles, uploads};
    };
    try {
        if (["target", "act", "scroll", "form", "submit", "live_prepare", "input_layout", "input_prepare", "input_check", "input_observe", "upload_prepare", "upload_observe"].includes(request.mode)) {
            const state = globalThis[request.state_key];
            if (!state || state.snapshot_id !== request.snapshot_id) return fail("stale_snapshot");
            for (const frame of state.frames) {
                frame.dirty ||= frame.observer.takeRecords().length > 0;
                if ((!state.live && frame.dirty) || frame.window.document !== frame.document ||
                    frame.document.URL !== frame.url) return fail("stale_snapshot");
            }
            if (request.mode === "scroll") {
                const frame = state.frames.find(f => f.frame_id === request.frame_id);
                if (!frame) return fail("frame_unavailable");
                const win = frame.window, before = [win.scrollX, win.scrollY];
                win.scrollBy({left: request.dx, top: request.dy, behavior: "instant"});
                const position = [Math.round(win.scrollX), Math.round(win.scrollY)];
                const changed = before[0] !== position[0] || before[1] !== position[1];
                return JSON.stringify({status: changed ? "applied" : "no_change", changed,
                    input_semantics: "cssom_scroll", scroll_position: position});
            }
            const target = state.targets.get(request.node_id);
            if (!target || !target.node.isConnected) return fail("stale_snapshot");
            if (state.live && (!target.fingerprint || liveFingerprint(target.node) !== target.fingerprint))
                return fail("stale_snapshot");
            if (["upload_prepare", "upload_observe"].includes(request.mode)) {
                const node = target.node, doc = node.ownerDocument;
                if (node.tagName !== "INPUT" || node.type !== "file" || doc.defaultView !== window ||
                    node.getRootNode() !== doc || node.multiple || node.webkitdirectory || node.matches(":disabled") ||
                    !node.getClientRects().length || getComputedStyle(node).visibility !== "visible")
                    return fail("control_unsupported");
                if (request.mode === "upload_prepare") {
                    node.focus({preventScroll:true});
                    return doc.activeElement === node ? JSON.stringify({prepared:true}) : fail("focus_unavailable");
                }
                if (doc.activeElement !== node || node.files.length !== 1 ||
                    node.files[0].name !== request.name || node.files[0].size !== request.size)
                    return fail("upload_outcome_unknown");
                globalThis.__steedUploadBindings ||= new WeakMap();
                globalThis.__steedUploadBindings.set(node, {id:request.upload_id, file:node.files[0]});
                return JSON.stringify({bound:true});
            }
            if (request.mode === "input_layout") return JSON.stringify({viewport_needed: innerWidth <= 0 || innerHeight <= 0});
            if (["input_prepare", "input_check", "input_observe"].includes(request.mode)) {
                const node = target.node, doc = node.ownerDocument, win = doc.defaultView;
                const tag = node.tagName.toLowerCase(), type = node.type;
                if (node.matches(":disabled") || node.getAttribute("aria-disabled") === "true" ||
                    node.hasAttribute("readonly") || node.getAttribute("aria-readonly") === "true" ||
                    !node.getClientRects().length || win.getComputedStyle(node).visibility !== "visible")
                    return fail("target_not_editable");
                if (request.mode !== "input_prepare" && doc.activeElement !== node) return fail("focus_unavailable");
                if (request.mode === "input_check") return JSON.stringify({valid: true});
                if (request.operation === "activate" && tag === "input" && ["checkbox", "radio"].includes(type)) {
                    if (request.mode === "input_observe") return JSON.stringify({checked: node.checked});
                    node.focus({preventScroll: true});
                    if (doc.activeElement !== node) return fail("focus_unavailable");
                    return JSON.stringify({before: node.checked, keys: ["space"], radio: type === "radio"});
                }
                if (request.operation === "select" && tag === "select" && !node.multiple) {
                    if (request.mode === "input_observe") return JSON.stringify({selected_index: node.selectedIndex});
                    const option = node.options[request.index];
                    const available = opt => !opt.disabled && !opt.closest("optgroup")?.disabled &&
                        !opt.hidden && win.getComputedStyle(opt).display !== "none";
                    if (!option || !available(option)) return fail("option_unavailable");
                    const keys = [];
                    if (node.selectedIndex !== request.index) {
                        keys.push("home");
                        for (let i = 0; i < request.index; i++) if (available(node.options[i])) keys.push("down");
                    }
                    node.focus({preventScroll: true});
                    if (doc.activeElement !== node) return fail("focus_unavailable");
                    return JSON.stringify({before: node.selectedIndex, keys});
                }
                return fail("control_unsupported");
            }
            if (request.mode === "live_prepare") {
                const node = target.node, doc = node.ownerDocument, win = doc.defaultView;
                if (win !== window || !["BUTTON", "INPUT"].includes(node.tagName) ||
                    (node.tagName === "INPUT" && !["button", "submit"].includes(node.type)) ||
                    node.matches(":disabled") || node.getAttribute("aria-disabled") === "true" ||
                    !node.getClientRects().length || win.getComputedStyle(node).visibility !== "visible")
                    return fail("control_unsupported");
                node.focus({preventScroll: false});
                return doc.activeElement === node ? JSON.stringify({prepared:true}) : fail("focus_unavailable");
            }
            if (["form", "submit"].includes(request.mode)) {
                const node = target.node, info = submission(node);
                if (info.error) return fail(info.error);
                if (request.mode === "form") return JSON.stringify(info);
                if (info.url !== request.expected.url || info.method !== request.expected.method ||
                    JSON.stringify(info.uploads) !== JSON.stringify(request.expected.uploads))
                    return fail("stale_snapshot");
                const form = node.form, win = node.ownerDocument.defaultView;
                const noValidate = Object.getOwnPropertyDescriptor(win.HTMLFormElement.prototype, "noValidate").get.call(form);
                if (!noValidate && !node.formNoValidate &&
                    !win.HTMLFormElement.prototype.checkValidity.call(form))
                    return JSON.stringify({status: "validation_failed"});
                win.HTMLFormElement.prototype.requestSubmit.call(form, node);
                return JSON.stringify({status: "submitted"});
            }
            if (request.mode === "act") {
                const node = target.node, doc = node.ownerDocument, win = doc.defaultView;
                const style = win.getComputedStyle(node);
                if (node.matches(":disabled") || node.getAttribute("aria-disabled") === "true" ||
                    node.hasAttribute("readonly") || node.getAttribute("aria-readonly") === "true" ||
                    !node.getClientRects().length || style.visibility !== "visible") return fail("target_not_editable");
                const tag = node.tagName.toLowerCase(), type = node.type;
                if (request.operation === "enter") {
                    const editor = state.live && win === window && node.isContentEditable && node.hasAttribute("contenteditable") &&
                        !["INPUT", "TEXTAREA", "SELECT", "OPTION"].includes(node.tagName) && node.getRootNode() === doc;
                    if (!(editor || tag === "textarea" || (tag === "input" && ["text", "search", "email", "url", "tel"].includes(type))))
                        return fail("control_unsupported");
                    const before = editor ? node.innerText : node.value;
                    node.focus({preventScroll: true});
                    if (editor) {
                        if (doc.activeElement !== node) return fail("focus_unavailable");
                        const range = doc.createRange(), selection = doc.getSelection();
                        if (!selection) return fail("focus_unavailable");
                        range.selectNodeContents(node); selection.removeAllRanges(); selection.addRange(range);
                    } else node.select();
                    if (doc.activeElement !== node) return fail("focus_unavailable");
                    const accepted = doc.execCommand(request.text ? "insertText" : "delete", false, request.text);
                    const value = editor ? node.innerText : node.value;
                    const changed = value !== before, matches = value === request.text;
                    return JSON.stringify({status: matches ? (changed ? "applied" : "no_change") :
                        (accepted ? "constrained" : "unsupported"), changed,
                        input_semantics: "chromium_editing_command", text_matches: matches});
                }
                return fail("operation_unsupported");
            }
            return JSON.stringify({valid: true});
        }

        const previous = globalThis[request.state_key];
        if (previous) for (const frame of previous.frames) frame.observer.disconnect();
        const state = {snapshot_id: request.snapshot_id, live: !!request.live, frames: [], targets: new Map()};
        globalThis[request.state_key] = state;
        const frames = [], limitations = new Set([
            request.live ? "normal_website_scripts_and_network" : "document_scripts_disabled",
            "form_values_omitted", "editable_text_omitted",
            "closed_shadow_roots_not_inspected", "canvas_content_not_inspected",
            "accessibility_tree_not_inspected", "hidden_accessible_references_omitted"
        ]);
        if (request.live) limitations.add("live_targets_revalidated_individually");
        const allowed = new Set(request.origins);
        let remainingText = 32000, remainingTargets = 200, remainingNodes = 12000;
        let remainingTargetText = 16000;
        let remainingShadowRoots = 32;
        const shadowRoots = new WeakSet();
        const shadowFor = (node, depth) => {
            const root = node.shadowRoot;
            if (!root) return null;
            limitations.add("open_shadow_root_present");
            if (depth >= 8 || (!shadowRoots.has(root) && remainingShadowRoots <= 0)) {
                limitations.add("shadow_root_limit"); return null;
            }
            if (!shadowRoots.has(root)) { shadowRoots.add(root); remainingShadowRoots--; }
            return root;
        };

        const clean = (text, limit = 256) => String(text || "").slice(0, limit * 4 + 64)
            .replace(/\s+/g, " ").trim().slice(0, limit);
        const excluded = node => {
            if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
            return /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|INPUT|TEXTAREA|SELECT|OPTION)$/.test(node.tagName) ||
                node.isContentEditable;
        };
        const hidden = (node, win) => {
            if (node.hidden || node.getAttribute("aria-hidden") === "true") return true;
            const style = win.getComputedStyle(node);
            return style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse";
        };
        const readable = (root, win, limit) => {
            const chunks = [];
            let length = 0;
            const walk = (node, shadowDepth = 0) => {
                if (remainingNodes-- <= 0) { limitations.add("node_limit"); return; }
                if (node.nodeType === Node.TEXT_NODE) {
                    if (node.nodeValue.length > limit - length) limitations.add("text_truncated");
                    const text = clean(node.nodeValue, Math.max(0, limit - length));
                    if (text) { chunks.push(text); length += text.length + 1; }
                    return;
                }
                if (node.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
                    for (const child of node.childNodes) {
                        if (length >= limit || remainingNodes <= 0) break;
                        walk(child, shadowDepth);
                    }
                    return;
                }
                if (node.nodeType !== Node.ELEMENT_NODE || excluded(node) || hidden(node, win)) return;
                if (node.tagName === "IMG") {
                    const alt = clean(node.getAttribute("alt"), Math.max(0, limit - length));
                    if (alt) { chunks.push(alt); length += alt.length + 1; }
                }
                for (const child of node.childNodes) {
                    if (length >= limit || remainingNodes <= 0) break;
                    walk(child, shadowDepth);
                }
                const shadow = shadowFor(node, shadowDepth);
                if (shadow && length < limit && remainingNodes > 0) walk(shadow, shadowDepth + 1);
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
        const referenceReadable = (node, win) => {
            let depth = 0;
            for (let ancestor = node; ancestor; ancestor = ancestor.parentElement || ancestor.getRootNode()?.host) {
                if (++depth > 128) { limitations.add("accessible_reference_depth_limit"); return ""; }
                if (remainingNodes-- <= 0) { limitations.add("node_limit"); return ""; }
                if (excluded(ancestor) || hidden(ancestor, win)) return "";
            }
            return readable(node, win, 256);
        };
        const labelFor = (node, doc, win) => {
            const labelled = (node.getAttribute("aria-labelledby") || "").slice(0, 2048).split(/\s+/).slice(0, 8)
                .map(id => node.getRootNode().getElementById(id)).filter(Boolean);
            const labelledText = clean(labelled.map(label => referenceReadable(label, win)).join(" "));
            if (labelledText) return labelledText;
            const aria = clean(node.getAttribute("aria-label"));
            if (aria) return aria;
            const labels = [];
            if (node.labels) for (let i = 0; i < Math.min(8, node.labels.length); i++) labels.push(node.labels[i]);
            const label = clean(labels.map(item => referenceReadable(item, win)).join(" "));
            return label || (excluded(node) ? "" : readable(node, win, 256)) || clean(node.getAttribute("title")) ||
                clean(node.getAttribute("placeholder")) || clean(node.getAttribute("name")) || node.tagName.toLowerCase();
        };
        const referenceText = (node, attribute, win) => {
            const root = node.getRootNode();
            const references = (node.getAttribute(attribute) || "").slice(0, 2048).split(/\s+/)
                .filter(Boolean).slice(0, 8).map(id => root.getElementById(id)).filter(Boolean);
            return clean(references.map(item => referenceReadable(item, win)).join(" "));
        };
        const descriptionFor = (node, label, win) => {
            const description = referenceText(node, "aria-describedby", win) || clean(node.getAttribute("aria-description"));
            const title = clean(node.getAttribute("title"));
            return description || (title !== label ? title : "");
        };
        const boundedTargetText = text => {
            const value = clean(text);
            if (value.length > remainingTargetText) limitations.add("target_text_truncated");
            const result = value.slice(0, remainingTargetText);
            remainingTargetText -= result.length;
            return result;
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
            const tracked = {window: win, document: doc, url, frame_id: frameId, dirty: false};
            tracked.observer = new win.MutationObserver(() => { tracked.dirty = true; });
            tracked.observer.observe(doc, {subtree: true, childList: true, characterData: true, attributes: true});
            state.frames.push(tracked);
            frame.text = readable(doc.body || doc.documentElement, win, remainingText);
            remainingText = Math.max(0, remainingText - frame.text.length);

            const frameElements = [];
            const inspectTree = (root, shadowDepth = 0) => {
                const walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
                let node = walker.currentNode;
                while (node && remainingNodes-- > 0) {
                    if (node.nodeType !== Node.ELEMENT_NODE) { node = walker.nextNode(); continue; }
                    // Observe an editor's boundary/label, never its text or descendants.
                    if (hidden(node, win)) {
                        node = walker.nextSibling();
                        while (!node && walker.parentNode()) node = walker.nextSibling();
                        continue;
                    }
                    const tag = node.tagName.toLowerCase(), type = clean(node.getAttribute("type"), 32).toLowerCase();
                    const editable = node.isContentEditable;
                    if ((tag === "iframe" || tag === "frame") && frameElements.length < 16) frameElements.push(node);
                    if (node.tagName === "SLOT") limitations.add("shadow_slot_order_approximate");
                    if (tag === "canvas") limitations.add("canvas_present");
                    if (["img", "svg", "video"].includes(tag)) limitations.add("visual_content_present");
                    const role = clean(node.getAttribute("role"), 64).toLowerCase();
                    let kind = "", operations = [], href = null, formAction = null, formMethod = null;
                    const constraints = [];
                    if (editable && !["input", "textarea", "select", "option"].includes(tag)) {
                        kind = "editable";
                        constraints.push("values_omitted");
                        limitations.add("editable_controls_present");
                        if (request.live && win === window && node.isContentEditable && node.hasAttribute("contenteditable")) {
                            operations = ["enter"];
                            constraints.push("website_handlers_can_change_data");
                        }
                        else constraints.push("editable_control_actions_unavailable");
                    } else if (tag === "a" && node.hasAttribute("href")) {
                        kind = "link";
                        const link = new URL(node.href, doc.baseURI);
                        if (link.href.length > 2048) constraints.push("link_url_too_long");
                        else if ((link.protocol === "http:" || link.protocol === "https:") && !link.username && !link.password) {
                            href = safeUrl(link.href); operations = ["navigate"];
                        } else constraints.push("unsupported_link_url");
                    } else if (tag === "button") { kind = "button"; operations = ["activate"]; }
                    else if (tag === "input" && type !== "hidden") {
                        kind = type === "checkbox" || type === "radio" ? type : "input";
                        operations = [kind === "input" ? "enter" : "activate"];
                        if (type === "password") { constraints.push("sensitive_field", "values_omitted"); operations = []; }
                        if (type === "file") {
                            constraints.push("values_omitted");
                            operations = request.upload_available && win === window && node.getRootNode() === doc &&
                                !node.multiple && !node.webkitdirectory ? ["upload"] : [];
                            if (!operations.length) constraints.push("file_actions_unavailable");
                        }
                        if (type === "submit" || type === "button") { kind = "button"; operations = ["activate"]; }
                    } else if (tag === "textarea") { kind = "textarea"; operations = ["enter"]; }
                    else if (tag === "select") { kind = "select"; operations = ["select"]; }
                    else if (["button", "link", "textbox", "combobox", "checkbox", "radio"].includes(role)) {
                        kind = "custom"; constraints.push("custom_control_unsupported");
                        limitations.add("custom_controls_present");
                    }
                    if (kind) {
                        if (kind === "button") {
                            const info = submission(node);
                            if (!info.error) {
                                operations = ["submit"];
                                const destination = new URL(info.url);
                                destination.search = ""; destination.hash = "";
                                formAction = destination.href; formMethod = info.method;
                            }
                            else if (request.live && !node.form && (tag === "button" || type === "button")) {
                                operations = ["activate"];
                                constraints.push("website_handlers_can_change_data");
                            }
                            else { operations = []; constraints.push(info.error); }
                        }
                        if (kind === "input" && !["text", "search", "email", "url", "tel", "password", "file"].includes(node.type))
                            constraints.push("control_unsupported");
                        if (kind === "select" && node.multiple) constraints.push("multiple_selection_unsupported");
                        if (node.getRootNode() !== doc) {
                            operations = [];
                            constraints.push("shadow_control_actions_unavailable");
                        }
                        if (remainingTargets <= 0) limitations.add("targets_truncated");
                        else {
                            const enabled = !node.matches(":disabled") && node.getAttribute("aria-disabled") !== "true";
                            const readOnly = node.hasAttribute("readonly") || node.getAttribute("aria-readonly") === "true";
                            if (!enabled) constraints.push("disabled");
                            if (readOnly) constraints.push("read_only");
                            const nodeId = "node:" + state.targets.size;
                            const fingerprint = request.live ? liveFingerprint(node) : null;
                            if (request.live && !fingerprint) {
                                constraints.push("live_target_context_limit");
                                limitations.add("live_target_context_limit");
                                operations = [];
                            }
                            state.targets.set(nodeId, {node, fingerprint});
                            const label = boundedTargetText(labelFor(node, doc, win));
                            const invalidState = (node.getAttribute("aria-invalid") || "false").trim().toLowerCase();
                            const invalid = invalidState !== "false" && invalidState !== "";
                            const required = (["input", "textarea", "select", "checkbox", "radio"].includes(kind) && node.hasAttribute("required")) ||
                                (node.getAttribute("aria-required") || "").trim().toLowerCase() === "true";
                            frame.targets.push({node_id: nodeId, kind, role: role || (kind === "editable" ? "textbox" : kind),
                                label, description: boundedTargetText(descriptionFor(node, label, win)), required, invalid,
                                error_message: invalid ? boundedTargetText(referenceText(node, "aria-errormessage", win)) : "",
                                enabled, read_only: readOnly, href,
                                operations, constraints, form_action: formAction, form_method: formMethod});
                            remainingTargets--;
                        }
                    }
                    if (editable) {
                        node = walker.nextSibling();
                        while (!node && walker.parentNode()) node = walker.nextSibling();
                        continue;
                    }
                    const shadow = shadowFor(node, shadowDepth);
                    if (shadow) {
                        tracked.observer.observe(shadow, {subtree: true, childList: true, characterData: true, attributes: true});
                        inspectTree(shadow, shadowDepth + 1);
                    }
                    node = walker.nextNode();
                }
            };
            inspectTree(doc.documentElement);
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
