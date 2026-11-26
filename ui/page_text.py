"""What an agent gets when it reads a tab: the page's text (reader_engines.
page_text_script), or for a PDF the PDF's own text.

A PDF shows in Chromium's viewer, which gives page scripts no text at all, so
Steed fetches the file from inside the tab (with the tab's own sign-in, as the
viewer did; usually from its cache) and reads it with Qt's PDF module (pdfium).
"""

import base64

from PyQt6 import sip
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QTimer

from browser import webengine

PDF_LIMIT = 40 * 1024 * 1024  # bytes
PDF_PAGES = 400
PDF_WAIT_MS = 20000
POLL_MS = 150
START_FETCH = """(() => {
  window.__steedPdfText = null;
  fetch(location.href).then(r => r.blob()).then(b => {
    if (b.size > LIMIT) { window.__steedPdfText = 'error:too large'; return; }
    const reader = new FileReader();
    reader.onload = () => { window.__steedPdfText = reader.result; };
    reader.onerror = () => { window.__steedPdfText = 'error:read'; };
    reader.readAsDataURL(b);
  }).catch(e => { window.__steedPdfText = 'error:' + e; });
  return true;
})()""".replace("LIMIT", str(PDF_LIMIT))


def pdf_text(data: bytes) -> str:
    """The text of a PDF's pages (the first PDF_PAGES), or "" if unreadable."""
    from PyQt6.QtPdf import QPdfDocument

    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    document = QPdfDocument(None)
    document.load(buffer)
    if document.status() != QPdfDocument.Status.Ready:
        return ""
    pages = min(document.pageCount(), PDF_PAGES)
    text = "\n\n".join(document.getAllText(i).text() for i in range(pages))
    document.close()
    return text


def read_page_text(page, config, callback):
    """Call callback(text) with what an agent should get from this page."""
    from ui.reader_engines import page_text_script

    def kind(content_type):
        if sip.isdeleted(page):
            callback("")
        elif content_type == "application/pdf":
            _read_pdf(page, callback)
        else:
            webengine.run_script(
                page, page_text_script(config), callback, world=webengine.STEED_WORLD
            )

    webengine.run_script(
        page, "document.contentType", kind, world=webengine.STEED_WORLD
    )


def _read_pdf(page, callback):
    waited = [0]

    def collected(value):
        if isinstance(value, str) and value.startswith("data:"):
            try:
                data = base64.b64decode(value.split(",", 1)[1])
            except (ValueError, IndexError):
                callback("")
                return
            callback(pdf_text(data))
        elif isinstance(value, str) and value.startswith("error:"):
            callback("")
        elif waited[0] >= PDF_WAIT_MS or sip.isdeleted(page):
            callback("")
        else:
            waited[0] += POLL_MS
            QTimer.singleShot(POLL_MS, poll)

    def poll():
        if sip.isdeleted(page):
            callback("")
            return
        webengine.run_script(
            page, "window.__steedPdfText", collected, world=webengine.STEED_WORLD
        )

    webengine.run_script(
        page, START_FETCH, lambda _: poll(), world=webengine.STEED_WORLD
    )
