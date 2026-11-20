"""WebMCP: tools that websites offer to agents, through any assistant.

Chrome is adding `document.modelContext.registerTool(...)` (WebMCP), with
which a site describes actions an agent can take there ("add to cart",
"search flights") instead of the agent clicking through its pages. Qt
WebEngine does not have it yet, so Steed defines it in each page's own
world. Sites that look for it then register their tools, and every agent
reaches them through two Steed tools: page.tools lists what the page offers,
page.call_tool runs one. Both go through the task's permissions: a tool the
site marks read-only needs read access to the page; any other needs the
task's submit permission, as pressing a button would. What a tool returns is
the site's content, not the user's, and is treated as untrusted.
"""

import json
import re

TOOL_LIMIT = 50
RESULT_LIMIT = 100_000
NAME = re.compile(r"[A-Za-z0-9_.:-]{1,100}")


def shim():
    """The page-world definition of document.modelContext."""
    return r"""
(() => {
    if (document.modelContext || window.__steedWebMCP) return;
    const tools = new Map(), results = new Map();
    const describe = ({execute, ...rest}) => rest;
    const api = {
        registerTool(tool, options = {}) {
            if (!tool || typeof tool.name !== "string" || typeof tool.execute !== "function")
                throw new TypeError("A tool needs a name and an execute function.");
            const entry = {
                name: tool.name.slice(0, 100),
                description: String(tool.description || "").slice(0, 2000),
                inputSchema: tool.inputSchema || {type: "object", properties: {}},
                annotations: Object.assign({}, tool.annotations || {}),
                execute: tool.execute,
            };
            tools.set(entry.name, entry);
            if (options && options.signal) options.signal.addEventListener("abort", () => {
                if (tools.get(entry.name) === entry) tools.delete(entry.name);
            });
            try { document.dispatchEvent(new Event("toolchange")); } catch (_) {}
        },
        unregisterTool(name) { tools.delete(String(name)); },
        getTools() { return [...tools.values()].map(describe); },
        async executeTool(tool, input) {
            const entry = tools.get(typeof tool === "string" ? tool : tool && tool.name);
            if (!entry) throw new Error("No such tool.");
            return entry.execute(input || {}, {});
        },
        // The earlier shape of the proposal, still used by some sites.
        provideContext(context = {}) {
            tools.clear();
            for (const tool of context.tools || []) api.registerTool(tool);
        },
        clearContext() { tools.clear(); },
    };
    Object.defineProperty(document, "modelContext", {value: api});
    if (!("modelContext" in navigator)) Object.defineProperty(navigator, "modelContext", {value: api});
    Object.defineProperty(window, "__steedWebMCP", {value: Object.freeze({
        list() {
            try { return JSON.stringify(api.getTools().slice(0, 50)); } catch (_) { return "[]"; }
        },
        call(id, name, input) {
            const entry = tools.get(name);
            if (!entry) { results.set(id, {error: "This page has no tool by that name."}); return; }
            results.set(id, {pending: true});
            Promise.resolve().then(() => entry.execute(JSON.parse(input), {})).then(value => {
                let text;
                try { text = JSON.stringify(value === undefined ? null : value); }
                catch (_) { text = JSON.stringify(String(value)); }
                results.set(id, {value: text.slice(0, 100000)});
            }, error => results.set(id, {error: String((error && error.message) || error).slice(0, 500)}));
        },
        result(id) {
            const found = results.get(id);
            if (found && !found.pending) results.delete(id);
            return JSON.stringify(found || {missing: true});
        },
    })});
})();
"""


def clean_tools(raw):
    """What a page says it offers, checked: [{name, description, input_schema,
    read_only, consequential}]."""
    try:
        listed = json.loads(raw) if isinstance(raw, str) else []
    except ValueError:
        return []
    found = []
    for tool in listed if isinstance(listed, list) else []:
        if not isinstance(tool, dict) or not NAME.fullmatch(str(tool.get("name", ""))):
            continue
        hints = (
            tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
        )
        schema = tool.get("inputSchema")
        found.append(
            {
                "name": tool["name"],
                "description": str(tool.get("description", ""))[:2000],
                "input_schema": schema if isinstance(schema, dict) else {},
                "read_only": hints.get("readOnlyHint") is True,
                "consequential": hints.get("consequentialHint") is True
                or hints.get("destructiveHint") is True,
            }
        )
        if len(found) >= TOOL_LIMIT:
            break
    return found
