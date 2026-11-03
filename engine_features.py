"""What Steed's web engine supports, checked in a real web page (steed:features).

Each feature is tested where websites would use it: an https page in a private
throwaway profile. A missing feature is explained as either newer than this
engine's Chromium (a Qt upgrade brings it) or left out of Qt WebEngine (no
upgrade will).
"""

import json

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage

import webengine

PROBE_URL = QUrl("https://steed-features.invalid/")
PROBE_TIMEOUT_MS = 15000
NEWER = "newer"  # arrives with a later Qt WebEngine
QT = "qt"  # Qt WebEngine leaves it out on Linux
# (group, name, JavaScript test, why it may be missing, what it is for)
FEATURES = (
    (
        "Video and audio",
        "H.264 video",
        "canPlay('video/mp4; codecs=\"avc1.42E01E\"')",
        "codecs",
        "Most MP4 videos and GIFs on Discord, Tenor and Giphy",
    ),
    (
        "Video and audio",
        "AAC audio",
        "canPlay('audio/mp4; codecs=\"mp4a.40.2\"', 'audio')",
        "codecs",
        "Sound in most MP4 videos",
    ),
    (
        "Video and audio",
        "VP9 video",
        "canPlay('video/webm; codecs=\"vp9\"')",
        QT,
        "YouTube and most WebM video",
    ),
    (
        "Video and audio",
        "AV1 video",
        "canPlay('video/mp4; codecs=\"av01.0.05M.08\"')",
        QT,
        "Newer YouTube and Netflix streams",
    ),
    (
        "Video and audio",
        "Opus audio",
        "canPlay('audio/webm; codecs=\"opus\"', 'audio')",
        QT,
        "Voice and video calls, WebM sound",
    ),
    (
        "Video and audio",
        "Streaming H.264 (MSE)",
        "!!(window.MediaSource && MediaSource.isTypeSupported('video/mp4; codecs=\"avc1.42E01E\"'))",
        "codecs",
        "Streaming sites that only offer MP4",
    ),
    (
        "Video and audio",
        "Document Picture-in-Picture",
        "'documentPictureInPicture' in window",
        QT,
        "Pop-out call controls, as in Google Meet",
    ),
    (
        "JavaScript",
        "Temporal (dates and times)",
        "typeof Temporal !== 'undefined'",
        NEWER,
        "The new date/time API",
    ),
    (
        "JavaScript",
        "Math.sumPrecise",
        "typeof Math.sumPrecise === 'function'",
        NEWER,
        "Exact sums of numbers",
    ),
    (
        "JavaScript",
        "Iterator.concat",
        "typeof Iterator !== 'undefined' && typeof Iterator.concat === 'function'",
        NEWER,
        "Joining iterators",
    ),
    (
        "JavaScript",
        "RegExp.escape",
        "typeof RegExp.escape === 'function'",
        NEWER,
        "Safe text in regular expressions",
    ),
    (
        "JavaScript",
        "Promise.try",
        "typeof Promise.try === 'function'",
        NEWER,
        "Promise helper",
    ),
    (
        "JavaScript",
        "Uint8Array.fromBase64",
        "typeof Uint8Array.fromBase64 === 'function'",
        NEWER,
        "Base64 decoding",
    ),
    (
        "JavaScript",
        "Resource management (using)",
        "typeof DisposableStack !== 'undefined'",
        NEWER,
        "Automatic cleanup",
    ),
    (
        "Page features",
        "View Transitions",
        "'startViewTransition' in document",
        NEWER,
        "Animated page changes",
    ),
    (
        "Page features",
        "Navigation API",
        "'navigation' in window",
        NEWER,
        "App-style navigation",
    ),
    (
        "Page features",
        "Speculation rules",
        "!!(HTMLScriptElement.supports && HTMLScriptElement.supports('speculationrules'))",
        NEWER,
        "Instant page loads",
    ),
    (
        "Page features",
        "Popover",
        "HTMLElement.prototype.hasOwnProperty('popover')",
        NEWER,
        "Menus and tooltips without scripts",
    ),
    (
        "Page features",
        "Invoker commands",
        "'command' in HTMLButtonElement.prototype",
        NEWER,
        "Buttons that open dialogs without scripts",
    ),
    (
        "Page features",
        "Interest invokers",
        "'interestForElement' in HTMLButtonElement.prototype",
        NEWER,
        "Hover popups without scripts",
    ),
    (
        "Page features",
        "Scheduler.yield",
        "typeof scheduler !== 'undefined' && 'yield' in scheduler",
        NEWER,
        "Smoother busy pages",
    ),
    (
        "Page features",
        "Digital Credentials",
        "typeof DigitalCredential !== 'undefined'",
        NEWER,
        "ID documents from a phone wallet",
    ),
    (
        "Styling (CSS)",
        "Anchor positioning",
        "CSS.supports('anchor-name: --a')",
        NEWER,
        "Attached popups and tooltips",
    ),
    (
        "Styling (CSS)",
        "if()",
        "CSS.supports('color: if(style(--x: 1): red; else: blue)')",
        NEWER,
        "Conditional styles",
    ),
    (
        "Styling (CSS)",
        "@function",
        "typeof CSSFunctionRule !== 'undefined'",
        NEWER,
        "Custom style functions",
    ),
    (
        "Styling (CSS)",
        "@scope",
        "typeof CSSScopeRule !== 'undefined'",
        NEWER,
        "Scoped styles",
    ),
    (
        "Styling (CSS)",
        "Customizable select",
        "CSS.supports('appearance: base-select')",
        NEWER,
        "Styled drop-down lists",
    ),
    (
        "Styling (CSS)",
        "Scroll-driven animations",
        "typeof ScrollTimeline !== 'undefined'",
        NEWER,
        "Animations that follow scrolling",
    ),
    (
        "Styling (CSS)",
        "corner-shape",
        "CSS.supports('corner-shape: squircle')",
        NEWER,
        "Rounded-square corners",
    ),
    (
        "Styling (CSS)",
        "random()",
        "CSS.supports('width: random(10px, 20px)')",
        NEWER,
        "Random values in styles",
    ),
    (
        "Devices and system",
        "WebGPU",
        "'gpu' in navigator",
        NEWER,
        "Fast 3D graphics and on-page AI",
    ),
    (
        "Devices and system",
        "WebHID",
        "'hid' in navigator",
        QT,
        "Keyboards, mice and game controllers",
    ),
    (
        "Devices and system",
        "Web Serial",
        "'serial' in navigator",
        QT,
        "Microcontrollers and serial devices",
    ),
    (
        "Devices and system",
        "WebUSB",
        "'usb' in navigator",
        QT,
        "Flashing firmware, configuring devices",
    ),
    (
        "Devices and system",
        "Web Bluetooth",
        "'bluetooth' in navigator",
        QT,
        "Bluetooth device setup",
    ),
    (
        "Devices and system",
        "File System Access",
        "'showOpenFilePicker' in window",
        NEWER,
        "Web editors saving to your files",
    ),
    (
        "Devices and system",
        "Local Font Access",
        "'queryLocalFonts' in window",
        NEWER,
        "Design tools using your fonts",
    ),
    (
        "Devices and system",
        "Web Share",
        "'share' in navigator",
        QT,
        "Share buttons (also absent in Chrome on Linux)",
    ),
    (
        "Sign-in and storage",
        "Security keys (WebAuthn)",
        "typeof PublicKeyCredential !== 'undefined'",
        NEWER,
        "USB and NFC security keys",
    ),
    (
        "Sign-in and storage",
        "Storage Access API",
        "'requestStorageAccess' in document",
        NEWER,
        "Embedded sign-ins, such as comment widgets",
    ),
    (
        "Sign-in and storage",
        "CookieStore",
        "'cookieStore' in window",
        NEWER,
        "Sites managing their cookies",
    ),
    (
        "Sign-in and storage",
        "Web Locks",
        "'locks' in navigator",
        NEWER,
        "Sites coordinating across tabs",
    ),
    (
        "Built-in AI",
        "Prompt API (LanguageModel)",
        "typeof LanguageModel !== 'undefined'",
        QT,
        "Google Chrome's Gemini Nano; not in Chromium",
    ),
    (
        "Built-in AI",
        "Translator API",
        "typeof Translator !== 'undefined'",
        QT,
        "Google Chrome's on-device translation",
    ),
)
REASONS = {
    NEWER: "Newer than this engine; a Qt WebEngine upgrade brings it.",
    QT: "Qt WebEngine leaves this out on Linux; an upgrade will not add it.",
    "codecs": "Not in the PyPI engine. Run build_codecs.py to add it (see DOCUMENTATION.md).",
}
_PROBE = (
    "(() => { const video = document.createElement('video'), "
    "audio = document.createElement('audio');"
    "const canPlay = (type, kind) => "
    "(kind === 'audio' ? audio : video).canPlayType(type) !== '';"
    "return JSON.stringify([%s]); })()"
)


def probe_script() -> str:
    checks = ",".join(
        f"(() => {{ try {{ return !!({test}); }} catch (e) {{ return false; }} }})()"
        for _, _, test, _, _ in FEATURES
    )
    return _PROBE % checks


def engine_summary() -> dict:
    import build_codecs

    target = build_codecs.wheel_dir()
    if build_codecs.using_build(target, thorough=False):
        codecs = "built"
    elif build_codecs.replaced_by_pip(target):
        codecs = "replaced"
    else:
        codecs = "pypi"
    return {**webengine.versions(), "codecs": codecs}


def results(raw) -> list[dict]:
    """Pair the page's answers with the feature list; bad answers count as missing."""
    try:
        answers = json.loads(raw) if isinstance(raw, str) else []
    except ValueError:
        answers = []
    if not isinstance(answers, list) or len(answers) != len(FEATURES):
        answers = [False] * len(FEATURES)
    return [
        {
            "group": group,
            "name": name,
            "use": use,
            "supported": answer is True,
            "reason": "" if answer is True else REASONS[reason],
            "kind": reason,
        }
        for (group, name, _, reason, use), answer in zip(FEATURES, answers, strict=True)
    ]


class FeatureProbe(QObject):
    """Loads a blank https page in a private profile and asks it."""

    finished = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profile = webengine.private_profile(self)
        self.page = QWebEnginePage(self.profile, self)
        self.done = False
        self.page.loadFinished.connect(self._loaded)
        self.timer = QTimer(self, singleShot=True, interval=PROBE_TIMEOUT_MS)
        self.timer.timeout.connect(lambda: self._finish(None))

    def start(self):
        self.timer.start()
        self.page.setHtml("<!doctype html><title>probe</title>", PROBE_URL)

    def _loaded(self, ok):
        if self.done:
            return
        if not ok:
            self._finish(None)
            return
        webengine.run_script(
            self.page, probe_script(), self._finish, world=webengine.PAGE_WORLD
        )

    def _finish(self, raw):
        if self.done:
            return
        self.done = True
        self.timer.stop()
        self.finished.emit(results(raw))
        # The page goes before its profile.
        page, self.page = self.page, None
        page.deleteLater()
        QTimer.singleShot(0, self.profile.deleteLater)
