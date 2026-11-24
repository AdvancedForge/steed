"""Fill saved addresses and cards into forms, and offer to save new ones.

A bar above the page offers to fill when a page shows address or card fields,
and to save after a person submits a new address or card. Nothing is filled
without the person's click; agents never fill card fields.
"""

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton

from browser import webengine
from browser.form_autofill import detect_script, fill_script, take_script
from browser.password_service import WORLD, frames, login_origin
from browser.password_vault import (
    AUTOFILL_FIELDS,
    VaultError,
    _autofill_record,
    address_label,
    card_label,
)

NOUN = {"addresses": "address", "cards": "card"}


def label(kind, item):
    return card_label(item) if kind == "cards" else address_label(item)


class FormBar(QFrame):
    """ "Fill your address?" or "Save this card?" above the page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PasswordBar")  # Same look as the password bar.
        self.setAccessibleName("Addresses and cards")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 8, 6)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self.choice = QComboBox(self)
        self.choice.setAccessibleName("Which one")
        row.addWidget(self.choice)
        self.accept = QPushButton(self)
        self.accept.setProperty("primary", True)
        self.dismiss = QPushButton("Not now", self)
        for button in (self.accept, self.dismiss):
            button.setAutoDefault(False)
            row.addWidget(button)
        self.hide()


class FormAutofill(QObject):
    def __init__(self, assistant):
        super().__init__(assistant)
        self.assistant = assistant
        self.window = assistant.window
        self.bar = FormBar(self.window)
        self.bar.accept.clicked.connect(self._accept)
        self.bar.dismiss.clicked.connect(self._dismiss)
        # ("fill", tab, kind, items) or ("save", tab, kind, record, existing id)
        self.offer = None
        # Sites where the fill offer was turned down, until Steed closes.
        self.declined = set()
        # Save offers waiting for the one showing (an address after a card).
        self.later = []

    @property
    def profile(self):
        return self.assistant.profile

    def _vault(self, *, ask=False):
        return self.assistant._vault(ask=ask)

    # ------------------------------------------------------------- saving

    def collect(self, tab, *, force):
        if (
            sip.isdeleted(tab)
            or not self.assistant._enabled("offer_save_autofill")
            or getattr(tab, "workspace_name", None)
            or tab.page() is None
        ):
            return
        for frame in frames(tab.page()):
            origin = login_origin(frame.url())
            if origin is None:
                continue
            webengine.run_in(
                frame,
                take_script(origin, force),
                WORLD,
                lambda result, tab=tab: self._captured(tab, result),
            )

    def _captured(self, tab, result):
        if (
            self.window._closing
            or sip.isdeleted(tab)
            or not isinstance(result, dict)
            or result.get("pending")
        ):
            return
        # A card first, then the address: one question at a time.
        offers = []
        for kind, key in (("cards", "card"), ("addresses", "address")):
            record = _autofill_record(kind, result.get(key))
            if record is None:
                continue
            existing = None
            vault = self.assistant.service.vault(self.profile)
            if vault is not None and vault.unlocked:
                known = False
                for item in vault.autofill(kind):
                    if self._same(kind, item, record):
                        known = all(
                            not record[name] or item[name] == record[name]
                            for name in AUTOFILL_FIELDS[kind]
                        )
                        existing = item["id"]
                if known:
                    continue  # Nothing new to keep.
            offers.append(("save", tab, kind, record, existing))
        if offers:
            self.later = offers[1:]
            self._show(offers[0])

    @staticmethod
    def _same(kind, item, record):
        if kind == "cards":
            return item["number"] == record["number"]
        return all(
            item[name].casefold() == record[name].casefold()
            for name in ("name", "street", "postal")
        )

    # ------------------------------------------------------------ filling

    def page_loaded(self, tab, ok):
        if not ok or sip.isdeleted(tab) or not self.assistant._enabled("form_autofill"):
            return
        # Checkout forms are often drawn by scripts after the page loads.
        for delay in (1200, 3500):
            QTimer.singleShot(delay, lambda tab=tab: self._detect(tab))

    def _detect(self, tab):
        if self.window._closing or sip.isdeleted(tab) or tab.page() is None:
            return
        if self.offer is not None and self.offer[1] is tab:
            return
        vault = self.assistant.service.vault(self.profile)
        if vault is None or not vault.unlocked:
            return
        top = login_origin(webengine.main_frame(tab.page()).url())
        if top is None or top in self.declined:
            return
        for frame in frames(tab.page()):
            origin = login_origin(frame.url())
            if origin is None:
                continue
            webengine.run_in(
                frame,
                detect_script(origin),
                WORLD,
                lambda counts, tab=tab, own=origin == top: self._detected(
                    tab, counts, own
                ),
            )

    def _detected(self, tab, counts, own_site):
        if (
            self.window._closing
            or sip.isdeleted(tab)
            or not isinstance(counts, dict)
            or (self.offer is not None and self.offer[1] is tab)
        ):
            return
        vault = self.assistant.service.vault(self.profile)
        if vault is None or not vault.unlocked:
            return
        # Card fields may sit in a payment provider's frame; addresses are
        # offered only in the site's own page.
        if counts.get("card_number") and vault.autofill("cards"):
            self._show(("fill", tab, "cards", vault.autofill("cards")))
        elif (
            own_site and counts.get("addresses", 0) >= 3 and vault.autofill("addresses")
        ):
            self._show(("fill", tab, "addresses", vault.autofill("addresses")))

    def fill(self, tab, kind, item):
        """Fill one saved entry into the current page (and, for cards, its
        payment frames)."""
        if sip.isdeleted(tab) or tab.page() is None:
            return
        vault = self._vault(ask=True)
        if vault is None:
            return
        try:
            vault.touch_autofill(kind, item["id"])
        except VaultError:
            pass
        top = login_origin(webengine.main_frame(tab.page()).url())
        values = {name: item[name] for name in AUTOFILL_FIELDS[kind]}
        counted = {"frames": 0, "filled": 0}
        targets = [
            (frame, origin)
            for frame in frames(tab.page())
            if (origin := login_origin(frame.url())) is not None
            and (kind == "cards" or origin == top)
        ]

        def done(count):
            counted["frames"] += 1
            counted["filled"] += count if isinstance(count, int) else 0
            if counted["frames"] == len(targets) and not self.window._closing:
                self.window.status_bar.showMessage(
                    f"Filled {label(kind, item)}"
                    if counted["filled"]
                    else f"No empty {NOUN[kind]} fields to fill on this page",
                    4000,
                )

        for frame, origin in targets:
            webengine.run_in(frame, fill_script(origin, kind, values), WORLD, done)
        if not targets:
            self.window.status_bar.showMessage(
                "Addresses and cards are filled on https sites", 4000
            )

    def menu_entries(self, menu, tab, vault):
        """Fill entries for the address bar key menu."""
        if vault is None:
            return
        added = False
        for kind in ("addresses", "cards"):
            for item in vault.autofill(kind)[:5]:
                if not added:
                    menu.addSeparator()
                    added = True
                menu.addAction(
                    f"Fill {label(kind, item)}",
                    lambda kind=kind, item=item: self.fill(tab, kind, item),
                )

    # ---------------------------------------------------------------- bar

    def _show(self, offer):
        replacing = self.offer is not None and self.bar.isVisible()
        self.offer = offer
        self.show()
        if replacing:
            # A page swapping the offer just before a click cannot steer it.
            self.bar.accept.setEnabled(False)
            QTimer.singleShot(1000, lambda: self.bar.accept.setEnabled(True))

    def show(self):
        offer = self.offer
        if offer is None:
            self.bar.hide()
            return
        tab = offer[1]
        if sip.isdeleted(tab) or tab not in self.window.tabs:
            self.offer = None
            self.bar.hide()
            return
        if tab is not self.window.tabs[self.window.current_tab_index]:
            self.bar.hide()
            return
        kind = offer[2]
        self.bar.choice.clear()
        if offer[0] == "fill":
            items = offer[3]
            for item in items[:10]:
                self.bar.choice.addItem(label(kind, item))
            self.bar.choice.setVisible(len(items) > 1)
            self.bar.label.setText(
                f"Fill your {NOUN[kind]}"
                + ("" if len(items) > 1 else f" ({label(kind, items[0])})")
                + "?"
            )
            self.bar.accept.setText("Fill")
        else:
            record, existing = offer[3], offer[4]
            self.bar.choice.hide()
            what = label(kind, record)
            self.bar.label.setText(
                f"Update your saved {NOUN[kind]} ({what})?"
                if existing
                else f"Save this {NOUN[kind]} ({what}) for next time?"
            )
            self.bar.accept.setText("Update" if existing else "Save")
        self.bar.show()

    def _next(self):
        """Show the next waiting save offer, if its tab is still open."""
        while self.later:
            offer = self.later.pop(0)
            if not sip.isdeleted(offer[1]):
                self._show(offer)
                return

    def _accept(self):
        offer, self.offer = self.offer, None
        self.bar.hide()
        QTimer.singleShot(0, self._next)
        if offer is None or sip.isdeleted(offer[1]):
            return
        if offer[0] == "fill":
            index = max(0, self.bar.choice.currentIndex())
            items = offer[3]
            self.fill(offer[1], offer[2], items[min(index, len(items) - 1)])
            return
        _, _, kind, record, existing = offer
        vault = self._vault(ask=True)
        if vault is None:
            return
        try:
            saved = vault.save_autofill(
                kind,
                {name: record[name] for name in AUTOFILL_FIELDS[kind]},
                existing,
            )
        except VaultError as error:
            self.window.status_bar.showMessage(str(error), 5000)
            return
        self.assistant.service.notify(self.profile)
        self.window.status_bar.showMessage(f"Saved {label(kind, saved)}", 4000)

    def _dismiss(self):
        offer, self.offer = self.offer, None
        self.bar.hide()
        QTimer.singleShot(0, self._next)
        if offer is not None and offer[0] == "fill" and not sip.isdeleted(offer[1]):
            page = offer[1].page()
            if page is not None:
                origin = login_origin(webengine.main_frame(page).url())
                if origin:
                    self.declined.add(origin)
