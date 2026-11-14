"""Websites' speech recognition (SpeechRecognition / webkitSpeechRecognition),
answered by Steed's dictation.

Qt WebEngine's own SpeechRecognition has no service behind it and starting it
ends the tab. Steed puts a script in each page (the page's world, before the
page's own scripts) with a replacement that passes requests to Steed through
the console with a token the page cannot learn. Steed decides the site from
the tab, asks for the microphone like any other site request (remembered in
Website Permissions), records and transcribes with the engine chosen in
Dictation Settings, and answers with the standard events. Only a tab's top
frame of a secure site is answered; frames inside it get the standard
"service-not-allowed" error.
"""

import json
import secrets

PREFIX = "\x01steedspeech "
TOKEN = secrets.token_hex(16)

SHIM = r"""
(() => {
  const TOKEN = __TOKEN__;
  const post = console.debug.bind(console);
  const answered = window.top === window && window.isSecureContext;
  const sessions = new Map();
  const states = new WeakMap();
  let made = 0;
  const HANDLERS = ["audiostart", "soundstart", "speechstart", "speechend",
    "soundend", "audioend", "result", "nomatch", "error", "start", "end"];
  const ask = (state, op) => post("\u0001steedspeech " + JSON.stringify({
    token: TOKEN, session: state.id, op, lang: state.lang,
    continuous: state.continuous, interim: state.interim }));
  const fire = (target, type, extra) => {
    let event;
    if (type === "error") {
      try { event = new SpeechRecognitionErrorEvent("error", extra); }
      catch (error) { event = new Event("error"); }
    } else {
      event = new Event(type);
    }
    for (const key of Object.keys(extra || {})) {
      if (event[key] !== extra[key]) Object.defineProperty(event, key, { value: extra[key] });
    }
    const handler = target["on" + type];
    if (typeof handler === "function") {
      try { handler.call(target, event); } catch (error) { setTimeout(() => { throw error; }); }
    }
    target.dispatchEvent(event);
  };
  const listed = (items) => {
    items.item = (index) => items[index] ?? null;
    return items;
  };
  const results = (items) => listed(items.map((entry) => {
    const alternatives = listed([{ transcript: entry.transcript, confidence: entry.final ? 0.9 : 0.5 }]);
    alternatives.isFinal = entry.final;
    return alternatives;
  }));
  class Recognition extends EventTarget {
    constructor() {
      super();
      states.set(this, { running: false, id: 0 });
      this.lang = "";
      this.continuous = false;
      this.interimResults = false;
      this.maxAlternatives = 1;
      this.grammars = typeof window.webkitSpeechGrammarList === "function"
        ? new window.webkitSpeechGrammarList() : [];
      for (const type of HANDLERS) this["on" + type] = null;
    }
    start() {
      const state = states.get(this);
      if (state.running) throw new DOMException("recognition has already started.", "InvalidStateError");
      state.running = true;
      state.id = ++made;
      state.lang = String(this.lang || document.documentElement.lang || navigator.language || "").slice(0, 35);
      state.continuous = !!this.continuous;
      state.interim = !!this.interimResults;
      sessions.set(state.id, this);
      if (!answered) {
        setTimeout(() => {
          window.__steedSpeechEvent(state.id, { type: "error", error: "service-not-allowed",
            message: "Speech recognition only works on the page itself, over https." });
          window.__steedSpeechEvent(state.id, { type: "end" });
        });
        return;
      }
      ask(state, "start");
    }
    stop() { const state = states.get(this); if (state.running) ask(state, "stop"); }
    abort() { const state = states.get(this); if (state.running) ask(state, "abort"); }
  }
  Object.defineProperty(window, "__steedSpeechEvent", { value: (id, event) => {
    const target = sessions.get(id);
    if (!target) return;
    if (event.type === "result") {
      fire(target, "result", { resultIndex: event.index, results: results(event.results) });
    } else if (event.type === "error") {
      fire(target, "error", { error: event.error, message: event.message || "" });
    } else {
      fire(target, event.type, {});
    }
    if (event.type === "end") {
      sessions.delete(id);
      states.get(target).running = false;
    }
  } });
  window.addEventListener("pagehide", () => {
    for (const target of sessions.values()) ask(states.get(target), "abort");
  });
  for (const name of ["SpeechRecognition", "webkitSpeechRecognition"]) {
    Object.defineProperty(window, name, { value: Recognition, writable: true, configurable: true });
  }
})();
"""


def shim():
    return SHIM.replace("__TOKEN__", json.dumps(TOKEN))


def parse(text):
    """A page's request, or None when it is not one of Steed's."""
    try:
        message = json.loads(text[len(PREFIX) :])
    except ValueError:
        return None
    if (
        not isinstance(message, dict)
        or message.get("token") != TOKEN
        or message.get("op") not in ("start", "stop", "abort")
        or type(message.get("session")) is not int
    ):
        return None
    language = message.get("lang")
    return {
        "op": message["op"],
        "session": message["session"],
        "lang": language[:35] if isinstance(language, str) else "",
        "continuous": message.get("continuous") is True,
        "interim": message.get("interim") is True,
    }
