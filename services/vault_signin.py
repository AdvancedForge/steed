"""Agents signing in with the person's saved passwords, without seeing them.

signin.request names a session on a sign-in page; Steed itself fills the
saved login for that exact site (and its one-time code, when the login has
one) and submits each step, as its own autofill would, then reports only the
outcome. The password, the code and even the username never reach the agent.
Access & Permissions → "Sign in with your saved passwords" decides: Never
refuses, Always signs in at once, Ask (the default) shows the site and lets
the person pick the account first. An approval covers that site for the task
for a few minutes, so a sign-in in several steps is asked about once. Not in
incognito, and only while the passwords are unlocked.
"""

import time
from dataclasses import dataclass, field

from PyQt6 import sip
from PyQt6.QtCore import QTimer

from browser import access_levels, webengine
from browser.data_panel_format import stable_id
from browser.password_service import (
    WORLD,
    code_script,
    fill_script,
    frames,
    login_origin,
    signin_form_script,
    signin_step_script,
    submit_script,
)
from browser.password_vault import VaultError, site_of, totp_code
from services.page_inspector import PageInspectionError
from services.task_policy import TaskPermissionError

APPROVAL_SECONDS = 180
MAX_STEPS = 3
SETTLE_MS = 800  # After a step's page loads, its fields may still be drawing.
LOAD_TIMEOUT_MS = 8000


@dataclass(frozen=True)
class SigninRequest:
    task_id: str
    request_id: str
    origin: str
    # (login id, username) for the person to choose from; never sent on.
    accounts: tuple = field(default=())
    state: str = "awaiting_approval"

    @property
    def request_key(self):
        return "signin:" + self.task_id + ":" + self.request_id

    @property
    def site(self):
        return site_of(self.origin)


class VaultSignin:
    def __init__(self, service):
        self.service = service
        self.requests = {}  # (task, request id) -> dict
        self.approved = {}  # (task, origin) -> (login id, until)

    # ------------------------------------------------------------ requests

    def _vault(self, profile):
        registry = self.service.profiles
        # A shared session names its profile ("existing:work"); a private
        # one uses the person's current profile's saved logins.
        profile = profile.removeprefix("existing:") if profile else "private"
        if profile == "private":
            owner = getattr(registry, "last_active_owner", None) or next(
                iter(getattr(registry, "owners", ()) or ()), None
            )
            profile = getattr(owner, "current_profile_name", "global") or "global"
        if profile == "search":
            raise ValueError("Saved passwords are not used in incognito.")
        passwords = getattr(registry, "passwords", None)
        vault = passwords.vault(profile) if passwords is not None else None
        if vault is None or not vault.unlocked:
            raise ValueError(
                "The person's saved passwords are locked. Ask them to unlock "
                "them in Steed, then try again."
            )
        return vault

    def _receipt(self, entry):
        receipt = {
            "request_id": entry["request_id"],
            "state": entry["state"],
            "site": site_of(entry["origin"]),
        }
        if entry["state"] == "done":
            receipt["outcome"] = entry["outcome"]
            receipt["steps"] = entry["steps"]
        return receipt

    def request(self, task_id, session_id, request_id):
        self.service._usable()
        self.service._owned(task_id, session_id)
        if not stable_id(request_id):
            raise ValueError("Invalid request ID")
        key = (task_id, request_id)
        if key in self.requests:
            entry = self.requests[key]
            if entry["session_id"] != session_id:
                raise ValueError("Request ID is already bound to another session")
            return self._receipt(entry)
        policy = access_levels.values(self.service.profiles.config, "agents")["signin"]
        if policy == "never":
            raise TaskPermissionError("signin_not_allowed")
        page, profile = self.service.backend.signin_page(session_id)
        origin = login_origin(page.url())
        if origin is None:
            raise ValueError("Saved logins are used on https sites only.")
        vault = self._vault(profile)
        if self.service.backend.inspect(session_id).mode != "live":
            # Sign-in pages run their own scripts and redirect after posting,
            # which restricted (scriptless) sessions never allow.
            raise ValueError(
                "Signing in needs a live session: session.open with mode='live'."
            )
        if not self.service.backend.signin_may_submit(session_id):
            # Signing in submits a form; that is the task's own permission.
            raise TaskPermissionError("submit_not_allowed")
        logins = vault.logins(origin)
        entry = {
            "task_id": task_id,
            "request_id": request_id,
            "session_id": session_id,
            "origin": origin,
            "profile": profile,
            "accounts": tuple((login.id, login.username) for login in logins),
            "state": "awaiting_approval",
            "outcome": "",
            "steps": 0,
            "created": time.time(),
        }
        self.requests[key] = entry
        self.service.store.record_event(
            task_id, "signin.requested", {"request_id": request_id, "origin": origin}
        )
        if not logins:
            self._finish(entry, "no_saved_login")
            return self._receipt(entry)
        remembered = self.approved.get((task_id, origin))
        if remembered and remembered[1] > time.monotonic():
            self._start(entry, remembered[0])
        elif policy == "always" or self.service.auto_approve_requests:
            self._start(entry, logins[0].id)
        else:
            self.service.changed.emit()
        return self._receipt(entry)

    def status(self, task_id, request_id):
        self.service._usable()
        entry = self.requests.get((task_id, request_id))
        if entry is None:
            raise KeyError("Sign-in request does not belong to this task")
        return self._receipt(entry)

    def pending(self, task_id):
        return [
            SigninRequest(
                task_id, e["request_id"], e["origin"], e["accounts"], e["state"]
            )
            for (task, _), e in self.requests.items()
            if task == task_id and e["state"] == "awaiting_approval"
        ]

    def resolve(
        self, task_id, request_id, *, approve=False, login_id=None, always=False
    ):
        """Trusted UI only: the person's answer, and the account they chose."""
        entry = self.requests.get((task_id, request_id))
        if entry is None or entry["state"] != "awaiting_approval":
            raise ValueError("This sign-in is no longer waiting.")
        if not approve:
            self._finish(entry, "denied", state="denied")
            return self._receipt(entry)
        if login_id not in {account[0] for account in entry["accounts"]}:
            raise ValueError("Choose one of the saved accounts.")
        if always:
            access_levels.set_policy(
                self.service.profiles.config, "agents", "signin", "always"
            )
        self._start(entry, login_id)
        return self._receipt(entry)

    def cancel_task(self, task_id, *, interrupted=False):
        for key in [k for k in self.requests if k[0] == task_id]:
            self.requests.pop(key)["state"] = "cancelled"
        for key in [k for k in self.approved if k[0] == task_id]:
            self.approved.pop(key)

    # ------------------------------------------------------------ signing in

    def _start(self, entry, login_id):
        entry["state"] = "signing_in"
        entry["login_id"] = login_id
        self.approved[(entry["task_id"], entry["origin"])] = (
            login_id,
            time.monotonic() + APPROVAL_SECONDS,
        )
        self.service.changed.emit()
        self._step(entry, None)

    def _finish(self, entry, outcome, *, state="done"):
        entry["state"], entry["outcome"] = state, outcome
        if self.requests.get((entry["task_id"], entry["request_id"])) is entry:
            self.service.store.record_event(
                entry["task_id"],
                "signin." + outcome,
                {
                    "request_id": entry["request_id"],
                    "origin": entry["origin"],
                    "steps": entry["steps"],
                },
            )
        self.service.changed.emit()

    def _live(self, entry):
        """("ok", page, frame) on the login's site; ("elsewhere"|"loading"|
        "not_loaded"|"gone", None, None) otherwise."""
        if self.requests.get((entry["task_id"], entry["request_id"])) is not entry:
            return "gone", None, None
        backend = self.service.backend
        try:
            snapshot = backend.inspect(entry["session_id"])
        except (KeyError, RuntimeError):
            return "gone", None, None
        if snapshot.loading:
            return "loading", None, None
        if snapshot.load_success is not True:
            return "not_loaded", None, None
        try:
            page, _ = backend.signin_page(entry["session_id"])
        except (
            KeyError,
            RuntimeError,
            ValueError,
            TaskPermissionError,
            PageInspectionError,
        ):
            return "gone", None, None
        if sip.isdeleted(page):
            return "gone", None, None
        frame = next(
            (f for f in frames(page) if login_origin(f.url()) == entry["origin"]),
            None,
        )
        if frame is None:
            return "elsewhere", None, None
        return "ok", page, frame

    def _step(self, entry, previous, waited=0):
        state, page, frame = self._live(entry)
        if state == "loading" and waited < LOAD_TIMEOUT_MS:
            QTimer.singleShot(
                SETTLE_MS, lambda: self._step(entry, previous, waited + SETTLE_MS)
            )
            return
        if state == "elsewhere" and entry["steps"]:
            # Sent on to another site after signing in (an app's own host).
            self._finish(entry, "signed_in")
            return
        if state != "ok":
            self._finish(
                entry,
                "page_did_not_load"
                if state in ("loading", "not_loaded")
                else "page_changed",
                state="failed",
            )
            return
        webengine.run_in(
            frame,
            signin_step_script(entry["origin"]),
            WORLD,
            lambda found: self._filled(entry, page, frame, found, previous),
        )

    def _filled(self, entry, page, frame, found, previous):
        if entry["state"] != "signing_in" or sip.isdeleted(page):
            return
        found = found if isinstance(found, dict) else {}
        kind = (
            "code"
            if found.get("code")
            else "password"
            if found.get("password")
            else "username"
            if found.get("user")
            else None
        )
        if kind is None:
            self._finish(entry, "signed_in" if entry["steps"] else "no_sign_in_form")
            return
        if kind == previous:
            # The same step again: a wrong password, a captcha or a check the
            # site wants from the person.
            self._finish(entry, "still_on_sign_in")
            return
        if entry["steps"] >= MAX_STEPS:
            self._finish(entry, "still_on_sign_in")
            return
        login = self._login(entry)
        if login is None:
            self._finish(entry, "no_saved_login", state="failed")
            return
        if kind == "code":
            if not login.totp:
                self._finish(entry, "code_needed_from_person")
                return
            try:
                script = code_script(entry["origin"], totp_code(login.totp)[0])
            except VaultError:
                self._finish(entry, "code_needed_from_person")
                return
        else:
            script = fill_script(
                entry["origin"], login.username, login.password, only_empty=False
            )
        webengine.run_in(frame, script, WORLD)
        entry["steps"] += 1
        self.service.store.record_event(
            entry["task_id"],
            "signin.filled",
            {
                "request_id": entry["request_id"],
                "origin": entry["origin"],
                "step": kind,
            },
        )
        webengine.run_in(
            frame,
            signin_form_script(entry["origin"]),
            WORLD,
            lambda target: self._submit(entry, page, frame, kind, target),
        )

    def _submit(self, entry, page, frame, kind, target):
        if entry["state"] != "signing_in" or sip.isdeleted(page):
            return
        if not (
            isinstance(target, dict)
            and isinstance(target.get("action"), str)
            and target.get("method") in ("GET", "POST")
        ):
            self._finish(entry, "could_not_submit")
            return
        try:
            self.service.backend.allow_signin_submission(
                entry["session_id"], target["action"], target["method"]
            )
        except (KeyError, RuntimeError, ValueError, TaskPermissionError):
            self._finish(entry, "could_not_submit")
            return
        webengine.run_in(
            frame,
            submit_script(entry["origin"]),
            WORLD,
            lambda result: self._submitted(entry, page, kind, result),
        )

    def _login(self, entry):
        try:
            vault = self._vault(entry["profile"])
        except ValueError:
            return None
        login = next(
            (
                login
                for login in vault.logins(entry["origin"])
                if login.id == entry.get("login_id")
            ),
            None,
        )
        if login is not None:
            vault.touch(login.id)
        return login

    def _submitted(self, entry, page, kind, result):
        if entry["state"] != "signing_in" or sip.isdeleted(page):
            return
        if not (isinstance(result, dict) and result.get("submitted")):
            self._finish(entry, "could_not_submit")
            return
        done = {"fired": False}

        def next_step(*_):
            if done["fired"]:
                return
            done["fired"] = True
            try:
                page.loadFinished.disconnect(next_step)
            except (TypeError, RuntimeError):
                pass
            QTimer.singleShot(SETTLE_MS, lambda: self._step(entry, kind))

        page.loadFinished.connect(next_step)
        # Sites that sign in without loading a new page still move on.
        QTimer.singleShot(LOAD_TIMEOUT_MS, next_step)
