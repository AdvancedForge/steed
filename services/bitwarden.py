"""Bitwarden as a source of saved logins, through its own command line (bw).

For people who keep their passwords in Bitwarden rather than Steed's vault:
Steed signs in with them the same way, filling the page itself so no agent
ever sees them. You unlock once per run (Steed passes your master password to
`bw unlock` in its environment, never on the command line); the session key
lives only in Steed's memory and goes away when you lock or Steed closes.
Steed never writes a Bitwarden password anywhere.
"""

import json
import os
import shutil
import subprocess
import time

from browser.password_service import login_origin
from browser.password_vault import Login

TIMEOUT = 30
CACHE_SECONDS = 60


class BitwardenError(RuntimeError):
    """A plain message to show the person."""


class Bitwarden:
    def __init__(self, program="bw"):
        self.program = program
        self.session = None
        self.cache = {}  # origin -> (time, [Login])

    @property
    def available(self):
        return shutil.which(self.program) is not None

    @property
    def unlocked(self):
        return self.session is not None

    def _run(self, *arguments, extra=None):
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("BW_")
        }
        environment["BW_NOINTERACTION"] = "true"
        environment.update(extra or {})
        try:
            done = subprocess.run(
                [self.program, *arguments],
                env=environment,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                check=False,
            )
        except FileNotFoundError:
            raise BitwardenError("Install the Bitwarden command line (bw).") from None
        except subprocess.TimeoutExpired:
            raise BitwardenError("Bitwarden did not answer in time.") from None
        return done

    def unlock(self, password):
        """Unlock with your master password; you must have run `bw login`."""
        if not isinstance(password, str) or not password:
            raise BitwardenError("Enter your Bitwarden master password.")
        done = self._run(
            "unlock",
            "--raw",
            "--passwordenv",
            "STEED_BW_PASSWORD",
            extra={"STEED_BW_PASSWORD": password},
        )
        session = done.stdout.strip()
        if done.returncode != 0 or not session or "\n" in session:
            message = (done.stderr or "").lower()
            if "not logged in" in message or "you are not logged in" in message:
                raise BitwardenError(
                    "Sign in to Bitwarden once in a terminal (bw login), then unlock here."
                )
            raise BitwardenError("Bitwarden did not unlock. Check the password.")
        self.session = session
        self.cache.clear()

    def lock(self):
        if self.session is not None:
            self._run("lock", extra={"BW_SESSION": self.session})
        self.session = None
        self.cache.clear()

    def logins(self, origin):
        """Logins whose saved address is exactly this site."""
        if self.session is None:
            return []
        cached = self.cache.get(origin)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return list(cached[1])
        done = self._run(
            "list", "items", "--url", origin, extra={"BW_SESSION": self.session}
        )
        if done.returncode != 0:
            if "locked" in (done.stderr or "").lower():
                self.session = None
                raise BitwardenError("Bitwarden locked itself. Unlock it again.")
            raise BitwardenError("Bitwarden could not list logins.")
        try:
            items = json.loads(done.stdout or "[]")
        except ValueError:
            raise BitwardenError(
                "Bitwarden sent something Steed could not read."
            ) from None
        found = []
        for item in items if isinstance(items, list) else []:
            login = item.get("login") if isinstance(item, dict) else None
            if not isinstance(login, dict) or not login.get("password"):
                continue
            uris = [
                u.get("uri") for u in login.get("uris") or [] if isinstance(u, dict)
            ]
            if not any(login_origin(u) == origin for u in uris if isinstance(u, str)):
                continue
            found.append(
                Login(
                    id="bw:" + str(item.get("id", ""))[:64],
                    origin=origin,
                    username=str(login.get("username") or ""),
                    password=str(login["password"]),
                    title=str(item.get("name") or "")[:200],
                    totp=str(login.get("totp") or ""),
                )
            )
        self.cache[origin] = (time.monotonic(), found)
        return list(found)


class Logins:
    """Steed's vault and Bitwarden together, as one place to find a login."""

    def __init__(self, vault=None, bitwarden=None):
        self.vault, self.bitwarden = vault, bitwarden

    def logins(self, origin):
        own = self.vault.logins(origin) if self.vault is not None else []
        try:
            outside = self.bitwarden.logins(origin) if self.bitwarden else []
        except BitwardenError:
            outside = []
        return list(own) + list(outside)

    def touch(self, login_id):
        if self.vault is not None and not login_id.startswith("bw:"):
            self.vault.touch(login_id)
