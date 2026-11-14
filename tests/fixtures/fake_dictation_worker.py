"""A stand-in for dictation_worker.py: the "audio" is UTF-8 text padded with
zero bytes, and the transcript is that text."""

import base64
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request["op"] == "load":
        if "broken" in request["dir"]:
            print(json.dumps({"id": None, "error": "FileNotFoundError: no model"}))
        else:
            print(json.dumps({"loaded": True}))
    else:
        audio = base64.b64decode(request["audio"]).rstrip(b"\0").decode()
        print(json.dumps({"id": request["id"], "text": audio}))
    sys.stdout.flush()
