"""Signing in on a page Steed itself has open, for a routine you allowed to.

A routine that meets a sign-in page can sign in with your saved login (from
Steed's vault or Bitwarden) when you ticked "Sign in with my saved login" for
it. The same rules as agents' vault sign-in apply: only on a form that looks
like signing in and sends to the same site, password and code filled by
Steed in its own script world, and a failed attempt's fields emptied.
"""

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from browser import webengine
from browser.password_service import (
    WORLD,
    clear_signin_script,
    code_script,
    fill_script,
    signin_step_script,
    submit_script,
)
from browser.password_vault import VaultError, totp_code

MAX_STEPS = 4
SETTLE_MS = 800
LOAD_TIMEOUT_MS = 8000


class PageSignin(QObject):
    """finished(outcome): signed_in, not_a_sign_in_form, still_on_sign_in,
    code_needed, could_not_submit or gone."""

    finished = pyqtSignal(str)

    def __init__(self, page, origin, login, parent=None):
        super().__init__(parent)
        self.page, self.origin, self.login = page, origin, login
        self.steps = 0
        self.done = False

    def start(self):
        self._step(None)

    def _finish(self, outcome):
        if not self.done:
            self.done = True
            self.finished.emit(outcome)

    def _frame(self):
        if sip.isdeleted(self.page):
            return None
        return webengine.main_frame(self.page)

    def _step(self, previous):
        frame = self._frame()
        if frame is None or self.done:
            self._finish("gone")
            return
        webengine.run_in(
            frame,
            signin_step_script(self.origin),
            WORLD,
            lambda found: self._found(found, previous),
        )

    def _found(self, found, previous):
        frame = self._frame()
        if frame is None or self.done:
            self._finish("gone")
            return
        found = found if isinstance(found, dict) else {}
        if found.get("unsafe"):
            self._finish("not_a_sign_in_form")
            return
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
            self._finish("signed_in" if self.steps else "not_a_sign_in_form")
            return
        if kind == previous or self.steps >= MAX_STEPS:
            webengine.run_in(frame, clear_signin_script(self.origin), WORLD)
            self._finish("still_on_sign_in")
            return
        if kind == "code":
            try:
                if not self.login.totp:
                    raise VaultError("no code")
                script = code_script(self.origin, totp_code(self.login.totp)[0])
            except VaultError:
                self._finish("code_needed")
                return
        else:
            script = fill_script(
                self.origin, self.login.username, self.login.password, only_empty=False
            )
        webengine.run_in(frame, script, WORLD)
        self.steps += 1
        webengine.run_in(
            frame,
            submit_script(self.origin),
            WORLD,
            lambda result: self._submitted(result, kind),
        )

    def _submitted(self, result, kind):
        if self.done or sip.isdeleted(self.page):
            self._finish("gone")
            return
        if not (isinstance(result, dict) and result.get("submitted")):
            self._finish("could_not_submit")
            return
        fired = []

        def next_step(*_):
            if fired or self.done:
                return
            fired.append(True)
            try:
                self.page.loadFinished.disconnect(next_step)
            except (TypeError, RuntimeError):
                pass
            QTimer.singleShot(SETTLE_MS, lambda: self._step(kind))

        self.page.loadFinished.connect(next_step)
        QTimer.singleShot(LOAD_TIMEOUT_MS, next_step)
