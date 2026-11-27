"""Search by meaning with your own embeddings service (Find a Page I Saw).

Any service that answers OpenAI's /v1/embeddings request works: Ollama or LM
Studio on this computer (nothing leaves it), llama.cpp's server, LocalAI, or a
hosted one such as OpenAI. Steed sends the text to embed (your description,
and each candidate page's title, site and, with Remember what I read on, the
start of its text) and ranks the pages itself. Vectors are kept in
<config>/embeddings.sqlite so a page is embedded once. A hosted service's key
stays in the desktop keyring, never in Steed's files.

In a test on 487 real pages, a small embedding model (bge-small) found the
page someone described first 93% of the time, against 80% for keyword search.
"""

import array
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

KEYRING_ATTRIBUTES = {"application": "steed", "purpose": "embeddings-api-key"}
BATCH = 32
MAX_TEXT = 2000
MAX_VECTORS = 20000
TIMEOUT_MS = 60000


def settings(config):
    return (
        str(config.get("embeddings_service", "") or "").strip(),
        str(config.get("embeddings_model", "") or "").strip(),
    )


def check_service(url):
    """The service's base address (…/v1), or ValueError."""
    from services.dictation import check_url

    found = check_url(url)
    return found.rstrip("/")


def api_key():
    from services import service_keys

    return service_keys.read(KEYRING_ATTRIBUTES["purpose"])


def set_api_key(value):
    """Keep (or with "" forget) the service's key in the keyring."""
    from services import service_keys

    service_keys.write(
        KEYRING_ATTRIBUTES["purpose"], "Steed embeddings service key", value
    )


def cosine_rank(query, vectors, limit=5):
    """Indexes of the vectors closest to query, best first."""

    def norm(v):
        return math.sqrt(sum(x * x for x in v)) or 1.0

    q_norm = norm(query)
    scores = []
    for index, vector in enumerate(vectors):
        if vector is None or len(vector) != len(query):
            continue
        dot = sum(a * b for a, b in zip(query, vector, strict=True))
        scores.append((dot / (q_norm * norm(vector)), index))
    scores.sort(reverse=True)
    return [index for _, index in scores[:limit]]


class VectorCache:
    """Vectors by model and text, so each page is embedded once."""

    def __init__(self, path):
        self.path = Path(path)
        self._db = None

    @property
    def db(self):
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.path))
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, "
                "vector BLOB NOT NULL, used REAL NOT NULL)"
            )
        return self._db

    @staticmethod
    def key(model, text):
        return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()

    def get(self, model, texts):
        keys = [self.key(model, t) for t in texts]
        found = {}
        for start in range(0, len(keys), 500):
            chunk = keys[start : start + 500]
            rows = self.db.execute(
                "SELECT key, vector FROM vectors WHERE key IN ("
                + ",".join("?" * len(chunk))
                + ")",
                chunk,
            )
            for key, blob in rows:
                vector = array.array("f")
                vector.frombytes(blob)
                found[key] = list(vector)
        return [found.get(k) for k in keys]

    def put(self, model, texts, vectors):
        now = time.time()
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO vectors VALUES (?, ?, ?)",
                [
                    (self.key(model, t), array.array("f", v).tobytes(), now)
                    for t, v in zip(texts, vectors, strict=True)
                ],
            )
            count = self.db.execute("SELECT count(*) FROM vectors").fetchone()[0]
            if count > MAX_VECTORS:
                self.db.execute(
                    "DELETE FROM vectors WHERE key IN (SELECT key FROM vectors "
                    "ORDER BY used LIMIT ?)",
                    (count - MAX_VECTORS,),
                )

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None


class Embedder(QObject):
    """Embeds texts with the configured service; emits done(vectors) with
    one vector per text, or failed(message)."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, config, parent=None, *, key=None):
        super().__init__(parent)
        service, self.model = settings(config)
        self.service = check_service(service) if service else ""
        self.key = api_key() if key is None else key
        self.cache = VectorCache(Path(config.config_dir) / "embeddings.sqlite")
        self.network = QNetworkAccessManager(self)
        self.reply = None

    def embed(self, texts):
        if not self.service or not self.model:
            self.failed.emit("Set up your embeddings service first.")
            return
        texts = [" ".join(str(t).split())[:MAX_TEXT] for t in texts]
        self.texts = texts
        self.vectors = self.cache.get(self.model, texts)
        self.missing = [i for i, v in enumerate(self.vectors) if v is None]
        self._next()

    def _next(self):
        if not self.missing:
            self.cache.close()
            self.done.emit(self.vectors)
            return
        batch, self.missing = self.missing[:BATCH], self.missing[BATCH:]
        request = QNetworkRequest(QUrl(self.service + "/embeddings"))
        request.setHeader(
            QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json"
        )
        if self.key:
            request.setRawHeader(b"Authorization", b"Bearer " + self.key.encode())
        request.setTransferTimeout(TIMEOUT_MS)
        body = json.dumps(
            {"model": self.model, "input": [self.texts[i] for i in batch]}
        ).encode()
        reply = self.network.post(request, body)
        self.reply = reply
        reply.finished.connect(lambda: self._answered(reply, batch))

    def _answered(self, reply, batch):
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            self.cache.close()
            if status in (401, 403):
                self.failed.emit("The embeddings service refused the key.")
                return
            self.failed.emit(
                f"The embeddings service did not answer ({status or reply.errorString()})."
                " Is it running, and is the model name right?"
            )
            return
        try:
            data = json.loads(bytes(reply.readAll()[: 64 * 1024 * 1024]))
            rows = sorted(data["data"], key=lambda row: row.get("index", 0))
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
            if len(vectors) != len(batch):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self.cache.close()
            self.failed.emit("The embeddings service's answer was not understood.")
            return
        for index, vector in zip(batch, vectors, strict=True):
            self.vectors[index] = vector
        self.cache.put(self.model, [self.texts[i] for i in batch], vectors)
        self._next()
