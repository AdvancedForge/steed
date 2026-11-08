// Steed's stand-ins for Chrome extension APIs that Qt WebEngine lacks.
//
// Steed adds this file to each extension it installs and runs it first in the
// extension's background service worker, its pages and its content scripts,
// so an extension that touches a missing API does not stop there.
//
// - tabs: Qt's chrome.tabs is nearly empty. Each content script keeps a port
//   to the background, which keeps the list of tabs from them: query, get,
//   sendMessage (delivered to the content script's runtime.onMessage), update
//   and the tab events work for pages the extension runs in. Extension pages
//   (popups) ask the background.
// - permissions: answered from the extension's own manifest.
// - notifications: the web Notification API.
// - menus, scripting, side panel, badges: succeed without effect.
// Existing APIs are never replaced.
(() => {
  const chrome = globalThis.chrome;
  if (!chrome || !chrome.runtime || !chrome.runtime.id || globalThis.__steedShim) return;
  globalThis.__steedShim = true;
  const worker =
    typeof ServiceWorkerGlobalScope !== "undefined" && globalThis instanceof ServiceWorkerGlobalScope;
  const page = !worker && location.protocol === "chrome-extension:";
  const content = !worker && !page;
  const PORT = "steed-tab";
  const BRIDGE = "steed-bridge";
  const manifest = chrome.runtime.getManifest ? chrome.runtime.getManifest() : {};

  const event = () => {
    const listeners = new Set();
    return {
      addListener: (f) => listeners.add(f),
      removeListener: (f) => listeners.delete(f),
      hasListener: (f) => listeners.has(f),
      hasListeners: () => listeners.size > 0,
      dispatch: (...args) => listeners.forEach((f) => f(...args)),
    };
  };
  // Chrome APIs take a callback or return a promise.
  const answer = (value, callback) => {
    if (typeof callback === "function") {
      setTimeout(() => callback(value), 0);
      return undefined;
    }
    return Promise.resolve(value);
  };
  const last = (args) => (typeof args[args.length - 1] === "function" ? args[args.length - 1] : null);
  const quiet = (value) => (...args) => answer(value, last(args));
  // A call with a trailing callback, or returning a promise, like Chrome.
  const settle = (args, run) => {
    const callback = last(args);
    if (callback) args = args.slice(0, -1);
    const result = Promise.resolve().then(() => run(args));
    if (callback) {
      result.then(callback, () => callback(undefined));
      return undefined;
    }
    return result;
  };

  const stubs = new Set();
  const add = (name, api) => {
    if (!chrome[name]) {
      chrome[name] = api;
      stubs.add(name);
    }
  };
  // Give a stand-in (never a real API) working members.
  const own = (name, members) => {
    if (stubs.has(name)) Object.assign(chrome[name], members);
  };
  // Fill in members missing from an API Qt has only in part.
  const fill = (name, members) => {
    if (!chrome[name]) chrome[name] = {};
    for (const [key, value] of Object.entries(members)) {
      if (chrome[name][key] === undefined) {
        try {
          chrome[name][key] = value;
        } catch (error) {
          // Read-only in this engine: leave it.
        }
      }
    }
  };
  // Show the extension's listeners its own messages and ports only, as Chrome
  // would pass them: change returns the arguments to pass, or null to hide.
  const screen = (event, change) => {
    if (!event || !event.addListener) return;
    const wrapped = new Map();
    const add = event.addListener.bind(event);
    const remove = event.removeListener.bind(event);
    const has = event.hasListener.bind(event);
    try {
      event.addListener = (listener) => {
        const filter = (...args) => {
          const passed = change(args);
          return passed ? listener(...passed) : undefined;
        };
        wrapped.set(listener, filter);
        add(filter);
      };
      event.removeListener = (listener) => remove(wrapped.get(listener) || listener);
      event.hasListener = (listener) => has(wrapped.get(listener) || listener);
    } catch (error) {
      // Not replaceable here: Steed's messages stay visible, and harmless.
    }
  };
  const ours = (message) => message && typeof message === "object" && message.__steed;
  const TAG = "\u0000steed:";
  // A content script's messages and ports carry its tab and frame (Qt leaves
  // sender.tab and sender.frameId out).
  const unwrapMessage = (tabOf) => ([message, sender, respond]) => {
    if (!ours(message)) return [message, sender, respond];
    if (message.__steed !== "envelope") return null;
    const tab = message.tab ? tabOf(message.tab, sender) : undefined;
    return [message.message, { ...sender, tab, frameId: message.frame }, respond];
  };
  const ports = new WeakMap();
  const unwrapPort = (tabOf) => ([port]) => {
    if (port.name === PORT || port.name === BRIDGE) return null;
    const at = port.name.indexOf(TAG);
    if (at < 0) return [port];
    if (!ports.has(port)) {
      const [tabId, frame] = port.name.slice(at + TAG.length).split(":").map(Number);
      const name = port.name.slice(0, at);
      const sender = { ...port.sender, tab: tabId ? tabOf(tabId, port.sender) : undefined, frameId: frame };
      ports.set(
        port,
        new Proxy(port, {
          get: (target, key) => {
            if (key === "name") return name;
            if (key === "sender") return sender;
            const value = Reflect.get(target, key);
            return typeof value === "function" ? value.bind(target) : value;
          },
        }),
      );
    }
    return [ports.get(port)];
  };

  syncStorage();
  if (content) {
    contentBridge();
    return;
  }

  // Qt WebEngine has chrome.storage.sync but every call fails ("not
  // available in this instance"). Keep "sync" data in local storage under a
  // prefix instead, shown to the extension as sync. (Steed's own sync between
  // computers does not carry it.)
  function syncStorage() {
    const storage = chrome.storage;
    if (!storage || !storage.local || !storage.onChanged) return;
    const local = storage.local;
    const prefix = "__steedSync:";
    const names = (keys) =>
      keys == null ? null : typeof keys === "string" ? [keys] : Array.isArray(keys) ? keys : Object.keys(keys);
    const localGet = local.get.bind(local);
    const unprefixed = (items, keep) => {
      const result = {};
      for (const [key, value] of Object.entries(items || {})) {
        if (key.startsWith(prefix) === keep) result[keep ? key.slice(prefix.length) : key] = value;
      }
      return result;
    };
    const onChanged = event();
    const area = {
      QUOTA_BYTES: 102400,
      QUOTA_BYTES_PER_ITEM: 8192,
      MAX_ITEMS: 512,
      MAX_WRITE_OPERATIONS_PER_HOUR: 1800,
      MAX_WRITE_OPERATIONS_PER_MINUTE: 120,
      get: (...args) =>
        settle(args, async ([keys]) => {
          const wanted = names(keys);
          const found = unprefixed(await localGet(wanted && wanted.map((key) => prefix + key)), true);
          const defaults = keys && typeof keys === "object" && !Array.isArray(keys) ? keys : {};
          return { ...defaults, ...found };
        }),
      set: (...args) =>
        settle(args, ([items]) =>
          local.set(Object.fromEntries(Object.entries(items || {}).map(([k, v]) => [prefix + k, v]))),
        ),
      remove: (...args) => settle(args, ([keys]) => local.remove(names(keys).map((key) => prefix + key))),
      clear: (...args) =>
        settle(args, async () => {
          const all = await localGet(null);
          await local.remove(Object.keys(all).filter((key) => key.startsWith(prefix)));
        }),
      getBytesInUse: quiet(0),
      setAccessLevel: quiet(undefined),
      onChanged,
    };
    try {
      Object.defineProperty(storage, "sync", { value: area, configurable: true, writable: true });
    } catch (error) {
      return;
    }
    // Local reads and change events leave the "sync" keys out.
    try {
      local.get = (...args) =>
        settle(args, async ([keys]) => (keys == null ? unprefixed(await localGet(null), false) : localGet(keys)));
    } catch (error) {
      // Read-only: the prefixed keys show in local.get(null).
    }
    const split = (changes) => [unprefixed(changes, true), unprefixed(changes, false)];
    storage.onChanged.addListener((changes, areaName) => {
      if (areaName !== "local") return;
      const [synced] = split(changes);
      if (Object.keys(synced).length) onChanged.dispatch(synced);
    });
    screenStorage(storage.onChanged, split);
  }

  function screenStorage(changed, split) {
    const wrapped = new Map();
    const add = changed.addListener.bind(changed);
    const remove = changed.removeListener.bind(changed);
    try {
      changed.addListener = (listener) => {
        const filter = (changes, areaName) => {
          if (areaName !== "local") return listener(changes, areaName);
          const [synced, rest] = split(changes);
          if (Object.keys(synced).length) listener(synced, "sync");
          if (Object.keys(rest).length) listener(rest, "local");
        };
        wrapped.set(listener, filter);
        add(filter);
      };
      changed.removeListener = (listener) => remove(wrapped.get(listener) || listener);
    } catch (error) {
      // Not replaceable: listeners see the prefixed keys as local.
    }
  }

  function contentBridge() {
    const top = window.top === window;
    const id = Math.floor(Math.random() * 2 ** 30) + 1;
    const frame = top ? 0 : id;
    // Frames cannot learn their tab's id, so only the top frame names one.
    const tabId = top ? id : 0;
    const sendMessage = chrome.runtime.sendMessage.bind(chrome.runtime);
    const connectTo = chrome.runtime.connect.bind(chrome.runtime);
    try {
      chrome.runtime.sendMessage = (...args) => {
        const at = typeof args[0] === "string" && /^[a-p]{32}$/.test(args[0]) && args.length > 1 ? 1 : 0;
        args[at] = { __steed: "envelope", tab: tabId, frame, message: args[at] };
        return sendMessage(...args);
      };
      chrome.runtime.connect = (...args) => {
        const at = typeof args[0] === "string" ? 1 : 0;
        const info = { ...(args[at] || {}) };
        info.name = `${info.name || ""}${TAG}${tabId}:${frame}`;
        args[at] = info;
        return connectTo(...args);
      };
    } catch (error) {
      // Read-only here: sender.tab stays missing.
    }
    if (!top) return; // Only the top frame stands for its tab.
    const listeners = new Set();
    const listen = chrome.runtime.onMessage.addListener.bind(chrome.runtime.onMessage);
    const unlisten = chrome.runtime.onMessage.removeListener.bind(chrome.runtime.onMessage);
    try {
      chrome.runtime.onMessage.addListener = (listener) => {
        listeners.add(listener);
        listen(listener);
      };
      chrome.runtime.onMessage.removeListener = (listener) => {
        listeners.delete(listener);
        unlisten(listener);
      };
    } catch (error) {
      return;
    }
    if (chrome.dom === undefined) {
      chrome.dom = { openOrClosedShadowRoot: (element) => element.openOrClosedShadowRoot?.() ?? element.shadowRoot };
    }
    const info = () => ({
      id,
      url: location.href,
      title: document.title,
      visible: document.visibilityState === "visible",
      focused: document.hasFocus(),
      status: document.readyState === "complete" ? "complete" : "loading",
    });
    let port = null;
    const send = (message) => {
      try {
        port && port.postMessage(message);
      } catch (error) {
        port = null;
      }
    };
    const deliver = (message, reply) => {
      // Like Chrome: the first answer wins; true or a promise means "later".
      let answered = false;
      let waiting = false;
      const respond = (response) => {
        if (!answered) {
          answered = true;
          reply(response);
        }
      };
      const sender = { id: chrome.runtime.id };
      for (const listener of listeners) {
        try {
          const result = listener(message, sender, respond);
          if (result === true) waiting = true;
          else if (result && typeof result.then === "function") {
            waiting = true;
            result.then(respond, () => respond(undefined));
          }
        } catch (error) {
          console.error(error);
        }
      }
      if (!waiting) respond(undefined);
    };
    const connect = () => {
      try {
        port = connectTo({ name: PORT });
      } catch (error) {
        return; // The extension was switched off or removed.
      }
      port.onMessage.addListener((message) => {
        if (message.type === "message") {
          deliver(message.message, (response) => send({ type: "response", ask: message.ask, response }));
        } else if (message.type === "navigate") {
          location.href = message.url;
        } else if (message.type === "reload") {
          location.reload();
        }
      });
      // The background stops when idle; connect again when it does.
      port.onDisconnect.addListener(() => {
        port = null;
        setTimeout(connect, 1000);
      });
      send({ type: "hello", tab: info() });
    };
    connect();
    let last = "";
    const update = () => {
      const tab = info();
      const now = JSON.stringify(tab);
      if (now !== last) {
        last = now;
        send({ type: "update", tab });
      }
    };
    for (const name of ["focus", "blur", "load", "pageshow"]) window.addEventListener(name, update);
    document.addEventListener("visibilitychange", update);
    document.addEventListener("readystatechange", update);
    // Titles and in-page address changes (single-page sites) have no event.
    setInterval(update, 1000);
  }

  const strings = (value) => (Array.isArray(value) ? value.filter((x) => typeof x === "string") : []);
  const asked = strings(manifest.permissions);
  const granted = {
    permissions: asked.filter((p) => !p.includes("://") && p !== "<all_urls>"),
    origins: [...strings(manifest.host_permissions), ...asked.filter((p) => p.includes("://") || p === "<all_urls>")],
  };
  const hostOf = (pattern) => (pattern === "<all_urls>" ? "*" : (pattern.split("://")[1] || "").split("/")[0]);
  const covers = (have, want) => {
    const host = hostOf(have);
    const wanted = hostOf(want);
    if (host === "*") return true;
    if (host.startsWith("*.")) return wanted === host.slice(2) || wanted.endsWith(host.slice(1));
    return host === wanted;
  };
  const holds = (query = {}) =>
    strings(query.permissions).every((p) => granted.permissions.includes(p)) &&
    strings(query.origins).every((o) => granted.origins.some((g) => covers(g, o)));
  add("permissions", {
    getAll: (callback) => answer({ permissions: [...granted.permissions], origins: [...granted.origins] }, callback),
    contains: (query, callback) => answer(holds(query), callback),
    // Steed grants only what the manifest asks for, at install.
    request: (query, callback) => answer(holds(query), callback),
    remove: (query, callback) => answer(false, callback),
    onAdded: event(),
    onRemoved: event(),
  });

  const menus = {
    create: (properties, callback) => {
      if (typeof callback === "function") setTimeout(callback, 0);
      return (properties && properties.id) || 1;
    },
    update: quiet(undefined),
    remove: quiet(undefined),
    removeAll: quiet(undefined),
    onClicked: event(),
    onShown: event(),
    onHidden: event(),
    ContextType: { ALL: "all", PAGE: "page", SELECTION: "selection", LINK: "link", IMAGE: "image", ACTION: "action" },
  };
  add("contextMenus", menus);

  add("notifications", {
    create: (...args) => {
      const callback = last(args);
      const id = typeof args[0] === "string" ? args[0] : `steed-${Date.now()}`;
      const options = (typeof args[0] === "string" ? args[1] : args[0]) || {};
      const title = options.title || manifest.name || "";
      const body = { body: options.message || "", icon: options.iconUrl, tag: id };
      try {
        if (globalThis.registration && globalThis.registration.showNotification) {
          globalThis.registration.showNotification(title, body);
        } else if (globalThis.Notification) {
          new Notification(title, body);
        }
      } catch (error) {
        // Notifications not allowed: like Chrome with them switched off.
      }
      return answer(id, callback);
    },
    update: quiet(false),
    clear: quiet(true),
    getAll: quiet({}),
    getPermissionLevel: quiet("granted"),
    onClicked: event(),
    onClosed: event(),
    onButtonClicked: event(),
  });

  const unavailable = (name) => (...args) => {
    const error = new Error(`chrome.${name} is not available in Steed`);
    const callback = last(args);
    if (callback) {
      setTimeout(() => callback(undefined), 0);
      return undefined;
    }
    return Promise.reject(error);
  };
  add("scripting", {
    executeScript: unavailable("scripting.executeScript"),
    insertCSS: unavailable("scripting.insertCSS"),
    removeCSS: quiet(undefined),
    registerContentScripts: quiet(undefined),
    unregisterContentScripts: quiet(undefined),
    updateContentScripts: quiet(undefined),
    getRegisteredContentScripts: quiet([]),
    ExecutionWorld: { ISOLATED: "ISOLATED", MAIN: "MAIN" },
  });
  add("userScripts", {
    register: quiet(undefined),
    unregister: quiet(undefined),
    update: quiet(undefined),
    getScripts: quiet([]),
    configureWorld: quiet(undefined),
  });
  add("webNavigation", {
    getFrame: quiet(null),
    getAllFrames: quiet([]),
    onBeforeNavigate: event(),
    onCommitted: event(),
    onDOMContentLoaded: event(),
    onCompleted: event(),
    onErrorOccurred: event(),
    onHistoryStateUpdated: event(),
    onReferenceFragmentUpdated: event(),
    onCreatedNavigationTarget: event(),
    onTabReplaced: event(),
  });
  add("sidePanel", {
    setOptions: quiet(undefined),
    getOptions: quiet({}),
    setPanelBehavior: quiet(undefined),
    getPanelBehavior: quiet({ openPanelOnActionClick: false }),
    open: unavailable("sidePanel.open"),
  });
  add("action", {
    setBadgeText: quiet(undefined),
    getBadgeText: quiet(""),
    setBadgeBackgroundColor: quiet(undefined),
    getBadgeBackgroundColor: quiet([0, 0, 0, 0]),
    setBadgeTextColor: quiet(undefined),
    setIcon: quiet(undefined),
    setTitle: quiet(undefined),
    getTitle: quiet(manifest.name || ""),
    setPopup: quiet(undefined),
    getPopup: quiet(""),
    enable: quiet(undefined),
    disable: quiet(undefined),
    isEnabled: quiet(true),
    getUserSettings: quiet({ isOnToolbar: true }),
    onClicked: event(),
  });
  add("commands", {
    getAll: quiet(
      Object.entries(manifest.commands || {}).map(([name, command]) => ({
        name,
        description: (command && command.description) || "",
        shortcut: (command && command.suggested_key && (command.suggested_key.linux || command.suggested_key.default)) || "",
      })),
    ),
    onCommand: event(),
  });
  add("fontSettings", { getFontList: quiet([]), getFont: quiet({ fontId: "" }) });
  add("extension", {
    getURL: (path) => chrome.runtime.getURL(path),
    isAllowedFileSchemeAccess: quiet(false),
    isAllowedIncognitoAccess: quiet(false),
    inIncognitoContext: false,
    getViews: () => [],
    getBackgroundPage: () => null,
  });

  // -- tabs, kept by the background from the content scripts' ports
  const tabEvents = {};
  for (const name of [
    "onCreated", "onUpdated", "onActivated", "onRemoved", "onReplaced", "onMoved",
    "onHighlighted", "onAttached", "onDetached", "onZoomChange",
  ]) {
    tabEvents[name] = event();
  }
  const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const matchUrl = (pattern, url) => {
    if (pattern === "<all_urls>") return /^(https?|wss?|ftp|file):/.test(url);
    const parts = /^(\*|https?|wss?|ftp|file):\/\/([^/]*)(\/.*)$/.exec(pattern);
    if (!parts) return false;
    const scheme = parts[1] === "*" ? "(https?|wss?)" : parts[1];
    const host =
      parts[2] === "*" ? "[^/]*" : parts[2].startsWith("*.") ? "([^/]*\\.)?" + escape(parts[2].slice(2)) : escape(parts[2]);
    const path = escape(parts[3]).replace(/\\\*/g, ".*");
    return new RegExp(`^${scheme}://${host}(:[0-9]+)?${path}$`).test(url);
  };
  const tabMembers = (api) => ({
    TAB_ID_NONE: -1,
    query: (...args) => api("query", args),
    get: (...args) => api("get", args),
    sendMessage: (...args) => api("sendMessage", args),
    update: (...args) => api("update", args),
    reload: (...args) => api("reload", args),
    getCurrent: quiet(undefined),
    remove: quiet(undefined),
    discard: quiet(undefined),
    getZoom: quiet(1),
    setZoom: quiet(undefined),
    captureVisibleTab: unavailable("tabs.captureVisibleTab"),
    ...tabEvents,
  });
  if (worker) {
    const tabs = new Map(); // id -> { port, tab }
    let focused = null;
    let asks = 0;
    const answers = new Map();
    const tabOf = (id) => {
      const known = tabs.get(id);
      if (!known) return undefined;
      const tab = known.tab;
      return {
        id,
        index: [...tabs.keys()].indexOf(id),
        windowId: 1,
        url: tab.url,
        title: tab.title,
        status: tab.status,
        active: id === focused,
        highlighted: id === focused,
        selected: id === focused,
        pinned: false,
        audible: false,
        discarded: false,
        autoDiscardable: true,
        incognito: false,
        groupId: -1,
        mutedInfo: { muted: false },
      };
    };
    const query = (q = {}) =>
      [...tabs.keys()].map(tabOf).filter(
        (tab) =>
          (q.active === undefined || tab.active === q.active) &&
          (q.highlighted === undefined || tab.highlighted === q.highlighted) &&
          (q.status === undefined || tab.status === q.status) &&
          (q.title === undefined || tab.title === q.title) &&
          (q.url === undefined || [].concat(q.url).some((p) => matchUrl(p, tab.url))) &&
          (q.windowId === undefined || q.windowId === 1 || q.windowId === -2) &&
          !q.pinned &&
          !q.audible,
      );
    const control = (id, message) => {
      try {
        tabs.get(id)?.port.postMessage(message);
      } catch (error) {
        // The tab went away meanwhile.
      }
    };
    const methods = {
      query: ([q]) => query(q),
      get: ([id]) => {
        const tab = tabOf(id);
        if (!tab) throw new Error(`No tab with id: ${id}.`);
        return tab;
      },
      sendMessage: ([id, message]) =>
        new Promise((resolve, reject) => {
          const known = tabs.get(id);
          if (!known) {
            reject(new Error("Could not establish connection. Receiving end does not exist."));
            return;
          }
          const ask = ++asks;
          answers.set(ask, resolve);
          known.port.postMessage({ type: "message", ask, message });
        }),
      update: ([id, props]) => {
        if (typeof id === "object") [id, props] = [focused, id];
        if (props && props.url) control(id, { type: "navigate", url: props.url });
        return tabOf(id);
      },
      reload: ([id]) => {
        control(id ?? focused, { type: "reload" });
      },
    };
    fill("tabs", {
      ...tabMembers((method, args) => settle(args, methods[method])),
      create: (...args) =>
        settle(args, ([props = {}]) => {
          globalThis.clients?.openWindow?.(props.url || "about:blank").catch(() => {});
          return { id: -1, url: props.url, active: true, windowId: 1 };
        }),
    });
    chrome.runtime.onConnect.addListener((port) => {
      if (port.name !== PORT) return;
      let id = null;
      port.onMessage.addListener((message) => {
        if (message.type === "response") {
          const resolve = answers.get(message.ask);
          answers.delete(message.ask);
          resolve && resolve(message.response);
          return;
        }
        const tab = message.tab;
        const before = tabs.get(tab.id)?.tab;
        id = tab.id;
        tabs.set(id, { port, tab });
        if (!before) tabEvents.onCreated.dispatch(tabOf(id));
        if (tab.visible && (tab.focused || focused === null) && focused !== id) {
          const previousTabId = focused ?? undefined;
          focused = id;
          tabEvents.onActivated.dispatch({ tabId: id, windowId: 1, previousTabId });
        }
        const changes = {};
        for (const key of ["url", "status", "title"]) {
          if (!before || before[key] !== tab[key]) changes[key] = tab[key];
        }
        if (Object.keys(changes).length) tabEvents.onUpdated.dispatch(id, changes, tabOf(id));
        listTabs();
      });
      port.onDisconnect.addListener(() => {
        if (id === null || tabs.get(id)?.port !== port) return;
        tabs.delete(id);
        if (focused === id) focused = null;
        tabEvents.onRemoved.dispatch(id, { windowId: 1, isWindowClosing: false });
        listTabs();
      });
    });
    // -- Steed itself, through the extension's bridge page (steed-bridge.html):
    // menus, badges, injected scripts and shortcuts.
    let bridge = null;
    const outbox = [];
    const toSteed = (message) => {
      if (bridge) {
        try {
          bridge.postMessage(message);
          return;
        } catch (error) {
          bridge = null;
        }
      }
      outbox.push(message);
      if (outbox.length > 1000) outbox.shift();
    };
    let listing = false;
    function listTabs() {
      if (listing) return;
      listing = true;
      setTimeout(() => {
        listing = false;
        toSteed({ type: "tabs", tabs: [...tabs.values()].map(({ tab }) => tab) });
      }, 50);
    }
    let asked = 0;
    const waiting = new Map();
    const askSteed = (message) =>
      new Promise((resolve, reject) => {
        const ask = ++asked;
        waiting.set(ask, { resolve, reject });
        toSteed({ ...message, ask });
      });
    const tabFor = (id) => (id ? tabOf(id) || { id, windowId: 1 } : undefined);

    const onclicks = new Map();
    let menuIds = 0;
    const plain = (props = {}) => {
      const { onclick, ...rest } = props;
      return JSON.parse(JSON.stringify(rest));
    };
    own("contextMenus", {
      create: (props = {}, callback) => {
        const id = props.id ?? `steed-${++menuIds}`;
        if (typeof props.onclick === "function") onclicks.set(id, props.onclick);
        toSteed({ type: "menu", op: "create", id, props: plain(props) });
        if (typeof callback === "function") setTimeout(callback, 0);
        return id;
      },
      update: (id, props = {}, callback) => {
        if (typeof props.onclick === "function") onclicks.set(id, props.onclick);
        toSteed({ type: "menu", op: "update", id, props: plain(props) });
        return answer(undefined, callback);
      },
      remove: (id, callback) => {
        onclicks.delete(id);
        toSteed({ type: "menu", op: "remove", id });
        return answer(undefined, callback);
      },
      removeAll: (callback) => {
        onclicks.clear();
        toSteed({ type: "menu", op: "removeAll" });
        return answer(undefined, callback);
      },
    });

    const badges = new Map(); // tab id, or "*" for every tab -> {text, color, title}
    const badgeOf = (tabId) => ({ ...(badges.get("*") || {}), ...(badges.get(tabId) || {}) });
    const setBadge = (details = {}, field, value, callback) => {
      const key = details.tabId ?? "*";
      badges.set(key, { ...(badges.get(key) || {}), [field]: value });
      toSteed({ type: "badge", tabId: details.tabId ?? null, field, value });
      return answer(undefined, callback);
    };
    const color = (value) =>
      Array.isArray(value)
        ? `rgba(${value[0]}, ${value[1]}, ${value[2]}, ${(value[3] ?? 255) / 255})`
        : String(value || "");
    own("action", {
      setBadgeText: (details = {}, callback) => setBadge(details, "text", String(details.text ?? ""), callback),
      getBadgeText: (details = {}, callback) => answer(badgeOf(details.tabId).text ?? "", callback),
      setBadgeBackgroundColor: (details = {}, callback) =>
        setBadge(details, "color", color(details.color), callback),
      getBadgeBackgroundColor: (details = {}, callback) => answer(badgeOf(details.tabId).color ?? "", callback),
      setTitle: (details = {}, callback) => setBadge(details, "title", String(details.title ?? ""), callback),
      getTitle: (details = {}, callback) =>
        answer(badgeOf(details.tabId).title ?? manifest.name ?? "", callback),
    });

    const injection = (spec = {}) => {
      const target = spec.target || {};
      const func = spec.func || spec.function;
      return {
        tabId: target.tabId,
        allFrames: Boolean(target.allFrames),
        world: spec.world === "MAIN" ? "MAIN" : "ISOLATED",
        func: typeof func === "function" ? func.toString() : spec.funcSource || null,
        args: spec.args || [],
        files: spec.files || [],
        css: spec.css || null,
      };
    };
    own("scripting", {
      executeScript: (...args) => settle(args, ([spec]) => askSteed({ type: "execute", ...injection(spec) })),
      insertCSS: (...args) => settle(args, ([spec]) => askSteed({ type: "css", op: "insert", ...injection(spec) })),
      removeCSS: (...args) => settle(args, ([spec]) => askSteed({ type: "css", op: "remove", ...injection(spec) })),
      registerContentScripts: (...args) =>
        settle(args, ([scripts]) => askSteed({ type: "register", op: "add", scripts: scripts || [] })),
      updateContentScripts: (...args) =>
        settle(args, ([scripts]) => askSteed({ type: "register", op: "update", scripts: scripts || [] })),
      unregisterContentScripts: (...args) =>
        settle(args, ([filter]) => askSteed({ type: "register", op: "remove", ids: filter && filter.ids })),
      getRegisteredContentScripts: (...args) =>
        settle(args, ([filter]) => askSteed({ type: "register", op: "list", ids: filter && filter.ids })),
    });

    const fromSteed = (message) => {
      if (message.type === "answer") {
        const asking = waiting.get(message.ask);
        waiting.delete(message.ask);
        if (asking) message.error ? asking.reject(new Error(message.error)) : asking.resolve(message.value);
      } else if (message.type === "menuClicked") {
        const tab = tabFor(message.tabId);
        onclicks.get(message.info.menuItemId)?.(message.info, tab);
        chrome.contextMenus.onClicked.dispatch(message.info, tab);
      } else if (message.type === "command") {
        chrome.commands.onCommand.dispatch(message.name, tabFor(message.tabId));
      } else if (message.type === "actionClicked") {
        chrome.action.onClicked.dispatch(tabFor(message.tabId));
      }
    };
    chrome.runtime.onConnect.addListener((port) => {
      if (port.name !== BRIDGE) return;
      bridge = port;
      port.onMessage.addListener(fromSteed);
      port.onDisconnect.addListener(() => {
        if (bridge === port) bridge = null;
      });
      for (const message of outbox.splice(0)) port.postMessage(message);
      listTabs();
    });
    // Pages' calls of these, made here.
    const callable = { contextMenus: true, action: true, scripting: true };

    // Popups and other extension pages ask here.
    chrome.runtime.onMessage.addListener((message, sender, respond) => {
      if (ours(message) && message.__steed === "call" && callable[message.ns]) {
        const method = chrome[message.ns][message.method];
        if (typeof method !== "function") return;
        Promise.resolve()
          .then(() => method(...(message.args || [])))
          .then(
            (ok) => respond({ ok }),
            (error) => respond({ error: String(error && error.message) }),
          );
        return true;
      }
      if (!ours(message) || message.__steed !== "tabs" || !methods[message.method]) return;
      Promise.resolve()
        .then(() => methods[message.method](message.args || []))
        .then(
          (ok) => respond({ ok }),
          (error) => respond({ error: String(error && error.message) }),
        );
      return true;
    });
    // A tab not (yet) in the list: what the sender says about it.
    const senderTab = (id, sender) =>
      tabOf(id) || { id, index: -1, windowId: 1, url: sender && sender.url, active: false, incognito: false };
    screen(chrome.runtime.onConnect, unwrapPort(senderTab));
    screen(chrome.runtime.onMessage, unwrapMessage(senderTab));
  } else if (page) {
    const ask = (method, args) =>
      settle(args, (rest) =>
        chrome.runtime.sendMessage({ __steed: "tabs", method, args: rest }).then((reply) => {
          if (!reply) throw new Error("Steed's tab list is not available.");
          if (reply.error) throw new Error(reply.error);
          return reply.ok;
        }),
      );
    fill("tabs", {
      ...tabMembers(ask),
      create: (...args) =>
        settle(args, ([props = {}]) => {
          window.open(props.url || "about:blank", "_blank");
          return { id: -1, url: props.url, active: true, windowId: 1 };
        }),
    });
    // Menus, badges and scripting are kept by the background: ask it.
    const call = (ns, method) => (...args) =>
      settle(args, (rest) =>
        chrome.runtime.sendMessage({ __steed: "call", ns, method, args: rest }).then((reply) => {
          if (!reply) throw new Error("The extension's background is not running.");
          if (reply.error) throw new Error(reply.error);
          return reply.ok;
        }),
      );
    const calls = (ns, names) => Object.fromEntries(names.map((name) => [name, call(ns, name)]));
    own("contextMenus", {
      ...calls("contextMenus", ["update", "remove", "removeAll"]),
      create: (props = {}, callback) => {
        const { onclick, ...rest } = props;
        const id = rest.id ?? `steed-page-${Math.random().toString(36).slice(2)}`;
        call("contextMenus", "create")({ ...rest, id }).then(() => callback && callback(), () => {});
        return id;
      },
    });
    own("action", calls("action", [
      "setBadgeText", "getBadgeText", "setBadgeBackgroundColor", "getBadgeBackgroundColor", "setTitle", "getTitle",
    ]));
    const scripting = calls("scripting", [
      "insertCSS", "removeCSS", "registerContentScripts", "updateContentScripts",
      "unregisterContentScripts", "getRegisteredContentScripts",
    ]);
    // Functions cannot travel in a message: send their source.
    scripting.executeScript = (spec = {}, ...rest) => {
      const { func, function: legacy, ...others } = spec;
      const source = func || legacy;
      return call("scripting", "executeScript")(
        { ...others, funcSource: typeof source === "function" ? source.toString() : undefined },
        ...rest,
      );
    };
    own("scripting", scripting);
    const pageTab = (id, sender) => ({ id, windowId: 1, url: sender && sender.url, incognito: false });
    screen(chrome.runtime.onConnect, unwrapPort(pageTab));
    screen(chrome.runtime.onMessage, unwrapMessage(pageTab));
  }
  add("windows", {
    WINDOW_ID_CURRENT: -2,
    WINDOW_ID_NONE: -1,
    get: quiet({ id: 1, focused: true, type: "normal" }),
    getCurrent: quiet({ id: 1, focused: true, type: "normal" }),
    getLastFocused: quiet({ id: 1, focused: true, type: "normal" }),
    getAll: quiet([{ id: 1, focused: true, type: "normal" }]),
    create: unavailable("windows.create"),
    update: quiet(undefined),
    remove: quiet(undefined),
    onCreated: event(),
    onRemoved: event(),
    onFocusChanged: event(),
    onBoundsChanged: event(),
  });
})();
