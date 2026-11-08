"""Translate a page's text through a service you choose (View → Translate Page).

Steed collects the page's visible text in its own script world, sends only that
text (no cookies, no page address) to a LibreTranslate-compatible service, and
writes the translations back in place. Show Original puts the text back. With
no service set, Steed offers Google Translate's page proxy instead, which sends
Google the page's address.
"""

import json
import re

from PyQt6 import sip
from PyQt6.QtCore import QLocale, QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest

import webengine

MAX_NODES = 2000
MAX_CHARS = 150_000
BATCH_NODES = 50
BATCH_CHARS = 5000
TIMEOUT_MS = 30000
_LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")

COLLECT = """(() => {
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'CODE', 'PRE', 'TEXTAREA', 'SVG']);
  const walker = document.createTreeWalker(document.body || document.documentElement,
    NodeFilter.SHOW_TEXT, {acceptNode(node) {
      for (let up = node.parentElement; up; up = up.parentElement) {
        if (skip.has(up.tagName) || up.isContentEditable) return NodeFilter.FILTER_REJECT;
      }
      return /\\p{L}/u.test(node.nodeValue) && node.nodeValue.trim().length > 1
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }});
  const nodes = [], texts = []; let chars = 0;
  for (let node = walker.nextNode(); node && nodes.length < MAX_NODES && chars < MAX_CHARS; node = walker.nextNode()) {
    nodes.push(node); texts.push(node.nodeValue.trim()); chars += node.nodeValue.length;
  }
  window.__steedTranslation = {nodes, original: nodes.map(n => n.nodeValue)};
  return JSON.stringify(texts);
})()""".replace("MAX_NODES", str(MAX_NODES)).replace("MAX_CHARS", str(MAX_CHARS))
APPLY = """((start, texts) => {
  const state = window.__steedTranslation; if (!state) return false;
  texts.forEach((text, i) => {
    const node = state.nodes[start + i], before = state.original[start + i];
    if (!node || typeof text !== 'string') return;
    const lead = before.match(/^\\s*/)[0], tail = before.match(/\\s*$/)[0];
    node.nodeValue = lead + text + tail;
  });
  return true;
})"""
RESTORE = """(() => {
  const state = window.__steedTranslation; if (!state) return false;
  state.nodes.forEach((node, i) => { node.nodeValue = state.original[i]; });
  window.__steedTranslation = null;
  return true;
})()"""


def target_language(config):
    chosen = str(config.get("translate_language", "") or "").strip()
    if chosen:
        return chosen
    return QLocale.system().name().split("_")[0] or "en"


def check_settings(service, language):
    if service and not re.match(r"^https?://[^\s]+$", service):
        raise ValueError(
            "Use an http(s) address for the translation service, or leave it blank."
        )
    if language and not _LANGUAGE.match(language):
        raise ValueError(
            "Use a language code such as en, de or pt-BR, or leave it blank."
        )


def google_proxy_url(url: QUrl, language: str) -> QUrl:
    target = QUrl("https://translate.google.com/translate")
    from PyQt6.QtCore import QUrlQuery

    query = QUrlQuery()
    for key, value in (("sl", "auto"), ("tl", language), ("u", url.toString())):
        query.addQueryItem(key, value)
    target.setQuery(query)
    return target


def batches(texts):
    """Groups of (start index, texts) within the service's request limits."""
    start, group, size = 0, [], 0
    for index, text in enumerate(texts):
        if group and (len(group) >= BATCH_NODES or size + len(text) > BATCH_CHARS):
            yield start, group
            start, group, size = index, [], 0
        group.append(text)
        size += len(text)
    if group:
        yield start, group


class PageTranslation(QObject):
    """Translates one page; progress(done, total), finished(), failed(text)."""

    progress = pyqtSignal(int, int)
    finished = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, page, service, language, parent=None):
        super().__init__(parent)
        self.page = page
        self.service = QUrl(service)
        self.language = language
        self.network = QNetworkAccessManager(self)
        self.pending = []
        self.total = 0
        self.done = 0
        self.stopped = False

    def start(self):
        webengine.run_script(
            self.page, COLLECT, self._collected, world=webengine.STEED_WORLD
        )

    def _collected(self, raw):
        try:
            texts = json.loads(raw) if isinstance(raw, str) else []
        except ValueError:
            texts = []
        if not texts:
            self._fail("This page has no text to translate.")
            return
        self.pending = list(batches(texts))
        self.total = len(texts)
        self._next()

    def _next(self):
        if self.stopped:
            return
        if not self.pending:
            self.stopped = True
            self.finished.emit()
            return
        start, texts = self.pending.pop(0)
        request = QNetworkRequest(self.service)
        request.setHeader(
            QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json"
        )
        request.setTransferTimeout(TIMEOUT_MS)
        # Only the text goes out: no cookies, no page address.
        for attribute in (
            QNetworkRequest.Attribute.CookieLoadControlAttribute,
            QNetworkRequest.Attribute.CookieSaveControlAttribute,
        ):
            request.setAttribute(attribute, QNetworkRequest.LoadControl.Manual)
        body = {"q": texts, "source": "auto", "target": self.language, "format": "text"}
        reply = self.network.post(request, json.dumps(body).encode())
        reply.finished.connect(lambda: self._answered(reply, start, texts))

    def _answered(self, reply, start, texts):
        data = reply.read(2_000_000)
        data = bytes(data) if data is not None else b""
        error = reply.error() != reply.NetworkError.NoError
        reply.deleteLater()
        if self.stopped:
            return
        try:
            translated = json.loads(data)["translatedText"]
            if isinstance(translated, str):
                translated = [translated]
            if (
                error
                or not isinstance(translated, list)
                or len(translated) != len(texts)
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self._fail("The translation service did not answer as expected.")
            return
        if sip.isdeleted(self.page):
            return
        webengine.run_script(
            self.page,
            f"{APPLY}({start}, {json.dumps([str(t) for t in translated])})",
            lambda _: None,
            world=webengine.STEED_WORLD,
        )
        self.done += len(texts)
        self.progress.emit(self.done, self.total)
        self._next()

    def _fail(self, message):
        if not self.stopped:
            self.stopped = True
            self.failed.emit(message)

    def cancel(self):
        self.stopped = True


def restore(page):
    webengine.run_script(page, RESTORE, lambda _: None, world=webengine.STEED_WORLD)
