"""Bounded viewport images through the bundled, private native frontend."""

import base64
import json
from concurrent.futures import Future
from uuid import uuid4

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer
from PyQt6.QtGui import QImageReader

from browser import webengine
from services.page_inspector import PageInspectionError

CAPTURE_SCRIPT = """
(() => {
    const id = /* ID */;
    window[id] = null;
    (async () => {
        try {
            const SDK = await import(new URL('./core/sdk/sdk.js', location.href).href);
            const manager = SDK.TargetManager.TargetManager.instance();
            const target = manager.primaryPageTarget() || manager.targets().find(t => t.type() === 'frame');
            if (!target || window[id] === false) throw new Error('Target unavailable');
            const agent = target.pageAgent();
            const metrics = await agent.invoke_getLayoutMetrics({});
            const viewport = metrics.cssVisualViewport;
            if (metrics.getError() || !viewport || window[id] === false) throw new Error('Viewport unavailable');
            const width = Math.min(1280, Math.floor(viewport.clientWidth));
            const height = Math.min(960, Math.floor(viewport.clientHeight));
            if (width < 1 || height < 1) throw new Error('Viewport unavailable');
            const response = await agent.invoke_captureScreenshot({format:'jpeg',quality:60,
                fromSurface:true,captureBeyondViewport:false,
                clip:{x:viewport.pageX,y:viewport.pageY,width,height,scale:1}});
            if (response.getError() || !response.data || response.data.length > 1500000 || window[id] === false)
                throw new Error('Capture unavailable');
            window[id] = JSON.stringify({data:response.data,
                css_width:width,css_height:height,
                viewport:Object.fromEntries(['pageX','pageY','offsetX','offsetY','clientWidth','clientHeight','scale'].map(k => [k,viewport[k]])),
                viewport_clipped:width < viewport.clientWidth || height < viewport.clientHeight});
        } catch (_) { if (window[id] !== false) window[id] = false; }
    })();
})();
"""


def image_payload(raw):
    """Validate dimensions before decoding, then bound model/transport image size."""
    if not isinstance(raw, str) or len(raw) > 1_600_000:
        raise PageInspectionError("capture_unavailable")
    data = json.loads(raw)
    if not isinstance(data, dict) or type(data.get("viewport_clipped")) is not bool:
        raise PageInspectionError("capture_unavailable")
    encoded = data.get("data")
    if not isinstance(encoded, str) or not 1 <= len(encoded) <= 1_500_000:
        raise PageInspectionError("capture_unavailable")
    try:
        contents = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise PageInspectionError("capture_unavailable") from None
    buffer = QBuffer()
    buffer.setData(QByteArray(contents))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"jpeg")
    size = reader.size()
    if not 1 <= size.width() <= 4096 or not 1 <= size.height() <= 4096:
        raise PageInspectionError("capture_dimensions_unsupported")
    scaled = size.scaled(1280, 960, Qt.AspectRatioMode.KeepAspectRatio)
    if scaled.width() < size.width() or scaled.height() < size.height():
        reader.setScaledSize(scaled)
    image = reader.read()
    if image.isNull():
        raise PageInspectionError("capture_unavailable")
    for quality in (70, 40):
        output = QBuffer()
        output.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(output, "JPEG", quality):
            raise PageInspectionError("capture_unavailable")
        if output.size() <= 256_000:
            return {
                "image": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(bytes(output.data())).decode("ascii"),
                },
                "width": image.width(),
                "height": image.height(),
                "css_width": data.get("css_width", size.width()),
                "css_height": data.get("css_height", size.height()),
                "viewport": data.get("viewport"),
                "viewport_clipped": data["viewport_clipped"],
                "source": "native_viewport",
                "limitations": [
                    "viewport_only",
                    "visible_values_and_embedded_content_included",
                    "site_content_untrusted",
                ],
            }
    raise PageInspectionError("capture_size_exceeded")


def capture_viewport(network, validate):
    """No caller scripts, viewport coordinates, URLs or paths are accepted."""
    result = Future()
    identifier = "__steed_capture_" + uuid4().hex
    poll = QTimer(network)
    poll.setInterval(30)
    deadline = QTimer(network)
    deadline.setSingleShot(True)

    def cleanup(_):
        poll.stop()
        deadline.stop()
        poll.deleteLater()
        deadline.deleteLater()
        if not network.closed:
            webengine.run_in(
                network.frontend, f"window[{json.dumps(identifier)}] = false"
            )

    def received(value):
        if result.done():
            return
        try:
            validate()
            network.validate()
            if value is None:
                return
            if value is False:
                raise PageInspectionError("capture_unavailable")
            payload = image_payload(value)
            validate()
            result.set_result(payload)
        except Exception as error:
            result.set_exception(error)

    def ready(source):
        if result.done():
            return
        try:
            source.result()
            validate()
            network.validate()
            webengine.run_in(
                network.frontend,
                CAPTURE_SCRIPT.replace("/* ID */", json.dumps(identifier)),
            )
            poll.start()
            deadline.start(5000)
        except Exception as error:
            result.set_exception(error)

    def tick():
        if not result.done():
            webengine.run_in(
                network.frontend, f"window[{json.dumps(identifier)}]", received
            )

    result.add_done_callback(cleanup)
    poll.timeout.connect(tick)
    deadline.timeout.connect(
        lambda: (
            result.set_exception(PageInspectionError("capture_timeout"))
            if not result.done()
            else None
        )
    )
    network.ready.add_done_callback(ready)
    return result
