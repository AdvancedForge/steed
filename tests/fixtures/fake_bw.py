#!/usr/bin/env python3
"""Synthetic Bitwarden command line: synthetic logins only, no account."""

import json
import os
import sys

SESSION = "synthetic-session-key"
arguments = sys.argv[1:]
if arguments[:1] == ["unlock"]:
    if (
        "--raw" in arguments
        and os.environ.get("STEED_BW_PASSWORD") == "synthetic-master"
    ):
        print(SESSION)
        sys.exit(0)
    print("Invalid master password.", file=sys.stderr)
    sys.exit(1)
if arguments[:1] == ["lock"]:
    sys.exit(0)
if arguments[:2] == ["list", "items"]:
    if os.environ.get("BW_SESSION") != SESSION:
        print("Vault is locked.", file=sys.stderr)
        sys.exit(1)
    url = arguments[arguments.index("--url") + 1]
    print(
        json.dumps(
            [
                {
                    "id": "item-1",
                    "name": "Example",
                    "login": {
                        "username": "synthetic-user@example.test",
                        "password": "Synthetic-Bw-Passw0rd",
                        "totp": "",
                        "uris": [{"uri": url + "/login"}],
                    },
                },
                {
                    "id": "item-2",
                    "name": "Lookalike",
                    "login": {
                        "username": "other",
                        "password": "Synthetic-Other",
                        "uris": [{"uri": "https://example.test.evil.invalid/"}],
                    },
                },
            ]
        )
    )
    sys.exit(0)
sys.exit(2)
