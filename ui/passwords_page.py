"""steed:passwords: the built-in password manager's page."""

import time
from secrets import token_hex

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog, QVBoxLayout, QWidget

from browser.password_service import copy_secret
from browser.password_vault import VaultError, VaultLocked, generate_password, totp_code
from ui.html_surface import HtmlSurface
from ui.prompts import MessageBox

# A shown password is hidden again after this long.
REVEAL_SECONDS = 30
SETTINGS = {
    "offer_save_passwords": "Offer to save passwords when you sign in",
    "password_autofill": "Fill saved logins automatically when a page loads",
    "steed_passkeys": "Keep passkeys in Steed (otherwise sites use security keys)",
}


class PasswordsPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.service = window.profile_manager.registry.passwords
        self.context = token_hex(16)
        self.sequence = 0
        self.revealed = {}
        self.generated = ""
        self.notice = self.error = ""
        # Password Checkup: login id -> findings, until the page closes.
        self.checkup = None
        self.breach_check = None
        # Hide revealed passwords on time, not only at the next action.
        self.expiry = QTimer(self)
        self.expiry.setSingleShot(True)
        self.expiry.timeout.connect(self.publish)
        self.disposed = False
        self.surface = HtmlSurface("passwords", self, label="passwords")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.surface)
        self.surface.command.connect(self.command)
        self.service.changed.connect(self._changed)
        self.window.profile_manager.profile_changed.connect(self._changed)
        self.publish()

    @property
    def profile(self):
        return self.window.profile_manager.current_profile_name

    def _changed(self, *_):
        self.revealed.clear()
        self.publish()

    def publish(self):
        if self.disposed:
            return
        vault = self.service.vault(self.profile)
        now = time.time()
        self.revealed = {k: v for k, v in self.revealed.items() if v[1] > now}
        if self.revealed:
            soonest = min(until for _, until in self.revealed.values())
            self.expiry.start(max(100, int((soonest - now) * 1000) + 50))
        logins, never, passkeys = [], [], []
        findings = self.checkup or {}
        state = (
            "private" if vault is None else "unlocked" if vault.unlocked else "locked"
        )
        if vault is not None and vault.unlocked:
            for login in vault.logins():
                logins.append(
                    {
                        "id": login.id,
                        "site": login.site,
                        "origin": login.origin,
                        "username": login.username,
                        "title": login.title,
                        "notes": login.notes,
                        "totp": bool(login.totp),
                        "password": self.revealed.get(login.id, (None,))[0],
                        "weak": login.id in findings.get("weak", []),
                        "reused": findings.get("reused", {}).get(login.id, 0),
                        "breached": findings.get("breached", {}).get(login.id, 0),
                    }
                )
            never = list(vault._need().never)
            for passkey in vault.passkeys():
                passkeys.append(
                    {
                        "id": passkey["id"],
                        "site": passkey["rp_id"],
                        "account": passkey["user_display"] or passkey["user_name"],
                        "username": passkey["user_name"],
                        "created": time.strftime(
                            "%Y-%m-%d", time.localtime(passkey["created"])
                        ),
                    }
                )
        self.surface.publish(
            {
                "context": self.context,
                "ack": self.sequence,
                "state": state,
                "profile": self.profile,
                "logins": logins,
                "never": never,
                "passkeys": passkeys,
                "checkup": None
                if self.checkup is None
                else {
                    "weak": len(findings.get("weak", [])),
                    "reused": len(findings.get("reused", {})),
                    "breached": len(findings.get("breached", {})),
                    "status": findings.get("status", ""),
                },
                "settings": [
                    {
                        "key": key,
                        "label": label,
                        "value": bool(self.window.config.get(key, True)),
                    }
                    for key, label in SETTINGS.items()
                ],
                "dark": self.window.profile_manager.registry.page_style.dark(
                    self.profile
                ),
                "notice": self.notice,
                "error": self.error,
                "generated": self.generated,
            },
            force=True,
        )
        self.generated = ""

    def command(self, command):
        if self.disposed or command.get("context") != self.context:
            return
        sequence = command.get("sequence")
        if type(sequence) is not int or sequence <= self.sequence:
            return
        self.sequence = sequence
        self.notice = self.error = ""
        try:
            self._run(command)
        except VaultLocked as error:
            self.error = str(error)
        except VaultError as error:
            self.error = str(error)
        except (OSError, UnicodeError):
            self.error = "The file could not be read or written."
        self.publish()

    def _vault(self):
        vault = self.service.vault(self.profile)
        if vault is None:
            raise VaultError("Incognito windows have no saved passwords.")
        if not vault.unlocked:
            raise VaultLocked("Unlock your passwords first.")
        return vault

    def _run(self, command):
        action = command.get("action")
        if action == "setting":
            if (
                command.get("key") not in SETTINGS
                or type(command.get("value")) is not bool
            ):
                raise VaultError("Unknown setting.")
            self.window.config.set(command["key"], command["value"])
            self.notice = "Saved."
            return
        if action == "unlock":
            vault = self.service.vault(self.profile)
            if vault is None:
                raise VaultError("Incognito windows have no saved passwords.")
            vault.unlock(str(command.get("password", "")))
            self.notice = "Unlocked."
            return
        vault = self._vault()
        if action == "reveal":
            login = vault.get(command.get("id"))
            self.revealed[login.id] = (login.password, time.time() + REVEAL_SECONDS)
        elif action == "hide":
            self.revealed.pop(command.get("id"), None)
        elif action == "copy":
            login = vault.get(command.get("id"))
            field = command.get("field")
            if field == "username":
                text, message = login.username, "Username copied."
            elif field == "password":
                text, message = login.password, "Password copied."
            elif field == "code" and login.totp:
                text, message = totp_code(login.totp)[0], "One-time code copied."
            else:
                raise VaultError("Nothing to copy.")
            copy_secret(text)
            self.notice = message + " It is cleared from the clipboard in 30 seconds."
        elif action == "save":
            fields = {
                name: str(command.get(name, ""))
                for name in ("origin", "username", "password", "title", "totp", "notes")
            }
            if command.get("id"):
                if not fields["password"]:
                    fields.pop("password")
                vault.update(command["id"], **fields)
                self.notice = "Login updated."
            else:
                origin = fields.pop("origin")
                if "://" not in origin:
                    origin = "https://" + origin
                vault.save(
                    origin, fields.pop("username"), fields.pop("password"), **fields
                )
                self.notice = "Login added."
            self.service.notify(self.profile)
        elif action == "delete":
            login = vault.get(command.get("id"))
            reply = MessageBox.question(
                self.window,
                "Delete login",
                f"Delete the saved login for {login.site}"
                f"{' (' + login.username + ')' if login.username else ''}?",
            )
            if reply == MessageBox.StandardButton.Yes:
                vault.delete(login.id)
                self.notice = "Login deleted."
                self.service.notify(self.profile)
        elif action == "delete_passkey":
            passkey = next(
                (p for p in vault.passkeys() if p["id"] == command.get("id")), None
            )
            if passkey is None:
                raise VaultError("That passkey no longer exists.")
            reply = MessageBox.question(
                self.window,
                "Delete passkey",
                f"Delete the passkey for {passkey['rp_id']}"
                f"{' (' + passkey['user_name'] + ')' if passkey['user_name'] else ''}?"
                " You will need another way to sign in there.",
            )
            if reply == MessageBox.StandardButton.Yes:
                vault.delete_passkey(passkey["id"])
                self.notice = "Passkey deleted."
                self.service.notify(self.profile)
        elif action == "checkup":
            from browser.password_checkup import BreachCheck, local_findings

            logins = vault.logins()
            self.checkup = {**local_findings(logins), "breached": {}}
            if command.get("online") is True and logins:
                self.checkup["status"] = "Checking for breaches…"
                self.breach_check = BreachCheck(logins, self)
                self.breach_check.finished.connect(self._breaches)
                self.breach_check.start()
            else:
                self.checkup["status"] = "Checked on this computer."
        elif action == "generate":
            self.generated = generate_password()
        elif action == "never_remove":
            vault.never_save(str(command.get("origin", "")), never=False)
            self.notice = "That site can offer to save passwords again."
        elif action == "import":
            path, _ = QFileDialog.getOpenFileName(
                self.window, "Import passwords", "", "CSV files (*.csv)"
            )
            if path:
                with open(path, encoding="utf-8-sig") as stream:
                    added, updated, skipped = vault.import_csv(stream.read())
                self.notice = f"Imported {added} new and {updated} updated login(s)" + (
                    f"; skipped {skipped} without a site or password."
                    if skipped
                    else "."
                )
                self.service.notify(self.profile)
        elif action == "export":
            reply = MessageBox.question(
                self.window,
                "Export passwords",
                "The exported file contains every password in plain text. Anyone "
                "who can read it can use your accounts. Export anyway?",
            )
            if reply != MessageBox.StandardButton.Yes:
                return
            path, _ = QFileDialog.getSaveFileName(
                self.window,
                "Export passwords",
                "steed-passwords.csv",
                "CSV files (*.csv)",
            )
            if path:
                import os

                # Never through a symlink, and owner-only even when replacing
                # an existing file.
                descriptor = os.open(
                    path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
                )
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(vault.export_csv())
                self.notice = "Exported. Delete the file once you no longer need it."
        else:
            raise VaultError("Unsupported request.")

    def _breaches(self, results, error):
        if self.disposed or self.checkup is None:
            return
        self.checkup["breached"] = results
        self.checkup["status"] = error or (
            "Checked with Have I Been Pwned: "
            + (
                f"{len(results)} password(s) found in breaches. Change them."
                if results
                else "none of your passwords was found in breaches."
            )
        )
        self.breach_check = None
        self.publish()

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.expiry.stop()
        self.revealed.clear()
        self.service.changed.disconnect(self._changed)
        self.window.profile_manager.profile_changed.disconnect(self._changed)
        self.surface.dispose()
