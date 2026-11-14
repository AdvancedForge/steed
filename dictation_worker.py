"""Steed's on-device speech recognizer, in its own process.

Started by services.dictation so a model never makes the browser stutter. It
speaks JSON lines on stdin/stdout:

    {"op": "load", "kind": "moonshine_v2"|"whisper"|"transducer", "dir": "...",
     "threads": 4, "language": ""}             -> {"loaded": true} or {"error"}
    {"op": "decode", "id": 3, "rate": 16000, "format": "int16"|"float32",
     "channels": 1, "audio": "<base64>"}        -> {"id": 3, "text": "..."}

Audio never leaves this computer, and nothing is written to disk.
"""

import array
import base64
import json
import sys
from pathlib import Path

MAX_AUDIO_BYTES = 16000 * 4 * 2 * 180  # About three minutes of float stereo.


def reply(value):
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()


def find(directory, *patterns):
    """The first file matching a pattern, preferring int8 models."""
    for pattern in patterns:
        found = sorted(
            directory.glob(pattern), key=lambda p: ("int8" not in p.name, p.name)
        )
        if found:
            return str(found[0])
    raise FileNotFoundError(f"The model is missing {patterns[0]}")


def load(request):
    import sherpa_onnx

    directory = Path(request["dir"])
    threads = int(request.get("threads", 4))
    kind = request["kind"]
    recognizer = sherpa_onnx.OfflineRecognizer
    if kind == "moonshine_v2":
        return recognizer.from_moonshine_v2(
            encoder=find(directory, "encoder_model*.ort", "*encoder*.onnx"),
            decoder=find(directory, "decoder_model*.ort", "*decoder*.onnx"),
            tokens=find(directory, "tokens.txt", "*tokens*.txt"),
            num_threads=threads,
        )
    if kind == "whisper":
        return recognizer.from_whisper(
            encoder=find(directory, "*encoder*.onnx"),
            decoder=find(directory, "*decoder*.onnx"),
            tokens=find(directory, "*tokens*.txt"),
            language=str(request.get("language") or ""),
            task="transcribe",
            num_threads=threads,
        )
    if kind == "transducer":
        return recognizer.from_transducer(
            encoder=find(directory, "encoder*.onnx"),
            decoder=find(directory, "decoder*.onnx"),
            joiner=find(directory, "joiner*.onnx"),
            tokens=find(directory, "tokens.txt"),
            model_type="nemo_transducer",
            num_threads=threads,
        )
    raise ValueError(f"Unknown model kind {kind!r}")


def samples(request):
    """The audio as floats in [-1, 1], first channel only."""
    data = base64.b64decode(request["audio"])
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("Too much audio")
    if request.get("format") == "float32":
        values = array.array("f", data[: len(data) - len(data) % 4])
        scale = 1.0
    else:
        values = array.array("h", data[: len(data) - len(data) % 2])
        scale = 1 / 32768
    channels = max(1, int(request.get("channels", 1)))
    return [value * scale for value in values[::channels]]


def main():
    recognizer = None
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if request.get("op") == "load":
                recognizer = load(request)
                reply({"loaded": True})
            elif request.get("op") == "decode":
                if recognizer is None:
                    raise RuntimeError("No model is loaded")
                stream = recognizer.create_stream()
                audio = samples(request)
                if audio:
                    stream.accept_waveform(int(request.get("rate", 16000)), audio)
                    recognizer.decode_stream(stream)
                text = stream.result.text.strip() if audio else ""
                reply({"id": request.get("id"), "text": text})
        except Exception as error:  # Reported to Steed, never a crash.
            reply(
                {
                    "id": request.get("id") if isinstance(request, dict) else None,
                    "error": f"{type(error).__name__}: {error}"[:300],
                }
            )


if __name__ == "__main__":
    main()
