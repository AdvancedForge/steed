"""Security-key sign-in (WebAuthn) prompts: account, PIN, touch, failures.

Qt WebEngine talks to USB and NFC security keys (FIDO2/U2F) and asks the
browser for these steps. Passkeys stored on this computer or synced from a
phone are not available in Qt WebEngine on Linux.
"""

from PyQt6 import sip
from PyQt6.QtCore import QObject
from PyQt6.QtWebEngineCore import QWebEngineWebAuthUxRequest as Request
from PyQt6.QtWidgets import QLineEdit

from ui.prompts import InputDialog, MessageBox

State = Request.WebAuthUxState
FAILURES = {
    "Timeout": "The security key did not respond in time.",
    "KeyNotRegistered": "This security key is not registered with the site.",
    "KeyAlreadyRegistered": "This security key is already registered with the site.",
    "SoftPinBlock": "Too many wrong PINs. Remove and reinsert the key to try again.",
    "HardPinBlock": "The security key is locked after too many wrong PINs.",
    "AuthenticatorRemovedDuringPinEntry": "The security key was removed.",
    "AuthenticatorMissingResidentKeys": "This security key cannot store sign-ins for the site.",
    "AuthenticatorMissingUserVerification": "This security key has no PIN or fingerprint support the site needs.",
    "AuthenticatorMissingLargeBlob": "This security key lacks a feature the site needs.",
    "NoCommonAlgorithms": "The security key and the site have no method in common.",
    "StorageFull": "The security key has no room for another sign-in.",
    "UserConsentDenied": "The request was declined on the security key.",
    "WinUserCancelled": "The request was cancelled.",
}
PIN_ERRORS = {
    "WrongPin": "That PIN was wrong.",
    "TooShort": "That PIN is too short.",
    "InvalidCharacters": "That PIN has characters the key does not accept.",
    "SameAsCurrentPin": "Choose a PIN different from the current one.",
    "InternalUvLocked": "Fingerprint unlocking is locked; enter the PIN instead.",
}


def _name(value):
    return getattr(value, "name", str(value))


class WebAuthPrompts(QObject):
    """Drives one security-key request at a time for a window."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.request = None
        self.touch = None

    def handle(self, request):
        if self.request is not None and not sip.isdeleted(self.request):
            self.request.cancel()
        self.request = request
        request.stateChanged.connect(self._changed)
        self._changed(request.state())

    def _site(self):
        return self.request.relyingPartyId() or "this site"

    def _close_touch(self):
        if self.touch is not None and not sip.isdeleted(self.touch):
            self.touch.done(0)
        self.touch = None

    def _changed(self, state):
        request = self.request
        if request is None or sip.isdeleted(request):
            return
        if state != State.FinishTokenCollection:
            self._close_touch()
        if state == State.SelectAccount:
            names = list(request.userNames())
            name, accepted = InputDialog.getItem(
                self.window,
                "Choose an account",
                f"Sign in to {self._site()} as:",
                names,
                0,
                False,
            )
            if accepted and name in names:
                request.setSelectedAccount(name)
            else:
                request.cancel()
        elif state == State.CollectPin:
            self._pin(request)
        elif state == State.FinishTokenCollection:
            self._show_touch()
        elif state == State.RequestFailed:
            reason = _name(request.requestFailureReason())
            box = MessageBox(self.window)
            box.setWindowTitle("Security key")
            box.setText(FAILURES.get(reason, "The security key sign-in failed."))
            retry = box.addButton("Try again", MessageBox.ButtonRole.AcceptRole)
            box.addButton("Close", MessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is retry and not sip.isdeleted(request):
                request.retry()
        elif state in (State.Completed, State.Cancelled):
            self.request = None

    def _pin(self, request):
        pin = request.pinRequest()
        reason = _name(pin.reason)
        lines = []
        error = _name(pin.error)
        if error in PIN_ERRORS:
            lines.append(PIN_ERRORS[error])
        if reason in ("Set", "Change"):
            lines.append(
                f"Choose a new PIN for your security key (at least {pin.minPinLength} "
                "characters)."
            )
        else:
            lines.append(f"Enter your security key's PIN for {self._site()}.")
        if pin.remainingAttempts > 0 and reason == "Challenge":
            lines.append(f"{pin.remainingAttempts} attempts left.")
        text, accepted = InputDialog.getText(
            self.window,
            "Security key PIN",
            "\n".join(lines),
            QLineEdit.EchoMode.Password,
        )
        if not accepted or sip.isdeleted(request):
            if not sip.isdeleted(request):
                request.cancel()
            return
        if reason in ("Set", "Change"):
            again, accepted = InputDialog.getText(
                self.window,
                "Security key PIN",
                "Enter the new PIN again:",
                QLineEdit.EchoMode.Password,
            )
            if not accepted or again != text:
                if not sip.isdeleted(request):
                    request.cancel()
                MessageBox.warning(
                    self.window, "Security key", "The PINs did not match."
                )
                return
        request.setPin(text)

    def _show_touch(self):
        if self.touch is not None:
            return
        box = MessageBox(self.window)
        box.setWindowTitle("Security key")
        box.setText(f"Touch your security key to continue with {self._site()}.")
        box.addButton("Cancel", MessageBox.ButtonRole.RejectRole)
        box.setModal(False)
        box.rejected.connect(
            lambda: (
                self.request is not None
                and not sip.isdeleted(self.request)
                and self.request.state() == State.FinishTokenCollection
                and self.request.cancel()
            )
        )
        self.touch = box
        box.show()
