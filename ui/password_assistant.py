"""Offer to save logins, fill saved ones, and the address bar key menu.

One assistant per window. It collects logins the capture script noticed
(when a page navigates, or when a submitted form has gone), asks once in a bar
above the page, and fills saved logins and one-time codes after pages load.
"""

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
)

from browser import webengine
from browser.password_service import (
    WORLD,
    code_script,
    copy_secret,
    fill_script,
    frames,
    login_origin,
    new_password_script,
    take_script,
)
from browser.password_vault import (
    VaultError,
    VaultLocked,
    generate_password,
    site_of,
    totp_code,
)

POLL_MS = 1500


class PasswordBar(QFrame):
    """ "Save password for site?" with Save, Never and Not now."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PasswordBar")
        self.setAccessibleName("Save password")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 8, 6)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self.save = QPushButton("Save", self)
        self.never = QPushButton("Never for this site", self)
        self.dismiss = QPushButton("Not now", self)
        for button in (self.save, self.never, self.dismiss):
            button.setAutoDefault(False)
            row.addWidget(button)
        self.hide()


class PasswordAssistant(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.service = window.profile_manager.registry.password_service
        self.offer = None  # (tab, origin, username, password, existing login)
        self.bar = PasswordBar(window)
        self.bar.save.clicked.connect(self._accept)
        self.bar.never.clicked.connect(self._never)
        self.bar.dismiss.clicked.connect(self._dismiss)
        self.filled = {}
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self._collect_current)
        self.poll.start()

    # ----------------------------------------------------------- settings

    @property
    def profile(self):
        return self.window.profile_manager.current_profile_name

    def _enabled(self, key):
        return bool(self.window.config.get(key, True)) and self.profile != "search"

    def _vault(self, *, ask=False):
        vault = self.service.vault(self.profile)
        if vault is None:
            return None
        if not vault.unlocked and ask:
            from PyQt6.QtWidgets import QLineEdit

            from ui.prompts import InputDialog

            creating = vault.is_new()
            for _ in range(3):
                text, accepted = InputDialog.getText(
                    self.window,
                    "Create a master password" if creating else "Unlock passwords",
                    "No system keyring is available, so a master password protects "
                    "this profile's passwords (at least 8 characters). It cannot be "
                    "recovered if you forget it:"
                    if creating
                    else "Master password for this profile's passwords:",
                    QLineEdit.EchoMode.Password,
                )
                if not accepted:
                    return None
                if creating:
                    again, accepted = InputDialog.getText(
                        self.window,
                        "Create a master password",
                        "Type the master password again:",
                        QLineEdit.EchoMode.Password,
                    )
                    if not accepted:
                        return None
                    if again != text:
                        self.window.status_bar.showMessage(
                            "The master passwords did not match.", 5000
                        )
                        continue
                try:
                    vault.unlock(text)
                    break
                except VaultLocked as error:
                    self.window.status_bar.showMessage(str(error), 5000)
                    continue
        return vault if vault.unlocked else None

    # ------------------------------------------------------------ capture

    def navigating(self, tab):
        """A page is leaving: collect anything its forms were submitting."""
        self._collect(tab, force=True)

    def _collect_current(self):
        if self.window._closing or not self.window.tabs:
            return
        self._collect(self.window.tabs[self.window.current_tab_index], force=False)

    def _collect(self, tab, *, force):
        if (
            sip.isdeleted(tab)
            or not self._enabled("offer_save_passwords")
            or getattr(tab, "workspace_name", None)
        ):
            return
        page = tab.page()
        if page is None:
            return
        for frame in frames(page):
            origin = login_origin(frame.url())
            if origin is None:
                continue
            webengine.run_in(
                frame,
                take_script(origin, force),
                WORLD,
                lambda result, origin=origin, tab=tab: self._captured(
                    tab, origin, result
                ),
            )

    def _captured(self, tab, origin, result):
        if (
            self.window._closing
            or sip.isdeleted(tab)
            or not isinstance(result, dict)
            or result.get("pending")
            or not isinstance(result.get("password"), str)
            or not result["password"]
        ):
            return
        vault = self.service.vault(self.profile)
        username = str(result.get("username") or "")[:4096]
        password = result["password"][:4096]
        existing = None
        if vault is not None and vault.unlocked:
            if vault.never_saves(origin):
                return
            logins = vault.logins(origin)
            same_user = [login for login in logins if login.username == username]
            if same_user and same_user[0].password == password:
                vault.touch(same_user[0].id)
                return
            if same_user:
                existing = same_user[0]
            elif not username and len(logins) == 1:
                existing = logins[0]
        replacing = self.offer is not None and self.bar.isVisible()
        self.offer = (tab, origin, username, password, existing)
        self._show_offer()
        if replacing:
            # A page swapping the offer just before a click cannot steer it.
            for button in (self.bar.save, self.bar.never):
                button.setEnabled(False)
            QTimer.singleShot(1000, self._enable_bar)

    def _enable_bar(self):
        for button in (self.bar.save, self.bar.never):
            button.setEnabled(True)

    def _show_offer(self):
        if self.offer is None:
            self.bar.hide()
            return
        tab, origin, username, _, existing = self.offer
        if sip.isdeleted(tab) or tab not in self.window.tabs:
            self.offer = None
            self.bar.hide()
            return
        current = self.window.tabs[self.window.current_tab_index]
        if tab is not current:
            self.bar.hide()
            return
        who = f" as {username}" if username else ""
        if existing is not None:
            self.bar.label.setText(
                f"Update your saved password for {site_of(origin)}"
                f"{' (' + existing.username + ')' if existing.username else ''}?"
            )
            self.bar.save.setText("Update password")
        else:
            self.bar.label.setText(f"Save your password for {site_of(origin)}{who}?")
            self.bar.save.setText("Save")
        self.bar.show()

    def tab_changed(self):
        self._show_offer()

    def _accept(self):
        offer, self.offer = self.offer, None
        self.bar.hide()
        if offer is None:
            return
        _, origin, username, password, existing = offer
        vault = self._vault(ask=True)
        if vault is None:
            return
        try:
            if existing is not None:
                vault.update(existing.id, password=password)
            else:
                vault.save(origin, username, password)
        except VaultError as error:
            self.window.status_bar.showMessage(str(error), 5000)
            return
        self.service.notify(self.profile)
        self.window.status_bar.showMessage(
            f"Password saved for {site_of(origin)}", 4000
        )

    def _never(self):
        offer, self.offer = self.offer, None
        self.bar.hide()
        vault = self._vault(ask=True)
        if offer is not None and vault is not None:
            vault.never_save(offer[1])
            self.service.notify(self.profile)

    def _dismiss(self):
        self.offer = None
        self.bar.hide()

    # --------------------------------------------------------------- fill

    def page_loaded(self, tab, ok):
        """Fill the top page's most recently used login after it loads.

        Embedded frames and one-time codes are filled only when asked
        (Ctrl+Shift+L or the key menu): an embedding page must not get them
        filled silently.
        """
        if not ok or sip.isdeleted(tab) or not self._enabled("password_autofill"):
            return
        vault = self.service.vault(self.profile)
        if vault is None or not vault.unlocked:
            return
        # Sign-in forms rendered by scripts may appear a little later.
        for delay in (0, 1200, 3500):
            QTimer.singleShot(delay, lambda tab=tab: self._fill_top(tab, vault))

    def _fill_top(self, tab, vault):
        if sip.isdeleted(tab) or self.window._closing or not vault.unlocked:
            return
        page = tab.page()
        if page is None:
            return
        frame = webengine.main_frame(page)
        origin = login_origin(frame.url())
        logins = vault.logins(origin) if origin else []
        if logins:
            login = logins[0]
            webengine.run_in(
                frame, fill_script(origin, login.username, login.password), WORLD
            )

    # ---------------------------------------------------------- key menu

    def menu(self):
        """The address bar key menu for the current site."""
        menu = QMenu(self.window)
        tab = self.window.tabs[self.window.current_tab_index]
        page = tab.page()
        origin = (
            login_origin(webengine.main_frame(page).url()) if page is not None else None
        )
        if self.profile == "search":
            menu.addAction("Passwords are not saved in incognito").setEnabled(False)
            return menu
        vault = self._vault(ask=True)
        if vault is None:
            menu.addAction("Passwords are locked").setEnabled(False)
        elif origin is None:
            menu.addAction("Saved logins work on https sites").setEnabled(False)
        else:
            logins = vault.logins(origin)
            if not logins:
                menu.addAction(f"No saved logins for {site_of(origin)}").setEnabled(
                    False
                )
            for login in logins[:10]:
                name = login.username or "(no username)"
                menu.addAction(
                    f"Fill {name}",
                    lambda login=login: self._fill_now(tab, vault, login),
                )
                menu.addAction(
                    f"Copy password for {name}",
                    lambda login=login: self._copy(login.password, "Password copied"),
                )
                if login.totp:
                    try:
                        code, left = totp_code(login.totp)
                    except VaultError:
                        continue
                    menu.addAction(
                        f"Copy code {code[:3]} {code[3:]}  ({left}s)",
                        lambda code=code: self._copy(code, "One-time code copied"),
                    )
        if vault is not None and origin is not None:
            # Embedded sign-in frames of other sites, filled only on request.
            for frame_origin in self._frame_origins(tab, origin):
                for other in vault.logins(frame_origin)[:3]:
                    menu.addAction(
                        f"Fill {other.username or '(no username)'} in the "
                        f"{site_of(frame_origin)} frame",
                        lambda other=other: self._fill_now(tab, vault, other),
                    )
        menu.addSeparator()
        generate = QAction("Generate strong password", menu)
        generate.triggered.connect(lambda: self._generate(tab))
        menu.addAction(generate)
        menu.addAction(
            "Manage passwords…",
            lambda: self.window._new_tab(url="steed:passwords", return_tab=True),
        )
        return menu

    def _frame_origins(self, tab, top):
        found = []
        for frame in frames(tab.page())[1:]:
            origin = login_origin(frame.url())
            if origin and origin != top and origin not in found:
                found.append(origin)
        return found

    def fill_current(self):
        """Fill saved logins and codes in the page and its frames (Ctrl+Shift+L)."""
        tab = self.window.tabs[self.window.current_tab_index]
        vault = self._vault(ask=True)
        filled = False
        if vault is not None:
            seen = set()
            for frame in frames(tab.page()):
                origin = login_origin(frame.url())
                if origin and origin not in seen and vault.logins(origin):
                    seen.add(origin)
                    self._fill_now(tab, vault, vault.logins(origin)[0])
                    filled = True
        if not filled:
            self.window.status_bar.showMessage("No saved login for this site", 4000)

    def _fill_now(self, tab, vault, login):
        if sip.isdeleted(tab):
            return
        vault.touch(login.id)
        for frame in frames(tab.page()):
            if login_origin(frame.url()) != login.origin:
                continue
            webengine.run_in(
                frame,
                fill_script(
                    login.origin, login.username, login.password, only_empty=False
                ),
                WORLD,
            )
            if login.totp:
                try:
                    code = totp_code(login.totp)[0]
                except VaultError:
                    continue
                webengine.run_in(frame, code_script(login.origin, code), WORLD)

    def _generate(self, tab):
        password = generate_password()
        if not sip.isdeleted(tab) and tab.page() is not None:
            for frame in frames(tab.page()):
                origin = login_origin(frame.url())
                if origin is not None:
                    webengine.run_in(
                        frame, new_password_script(origin, password), WORLD
                    )
        self._copy(password, "Strong password filled and copied")

    def _copy(self, text, message):
        copy_secret(text)
        self.window.status_bar.showMessage(
            message + " (cleared from the clipboard in 30 seconds)", 4000
        )

    def show_menu(self):
        button = self.window.address_bar.passwords_btn
        menu = self.menu()
        self.window._show_html_menu(
            menu,
            button.mapToGlobal(button.rect().bottomLeft()),
            context=True,
        )
