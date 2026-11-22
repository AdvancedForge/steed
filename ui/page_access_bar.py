"""A bar above a page of yours that asked for something it may not have yet.

Your own steed:<name> pages reach Steed's data through fetch("/api/…"), and
Access → Local pages leaves that at Never until you choose. Rather than a trip
to Access, the page's request shows here: what it wants, in words, with Allow
(for this page only, remembered with it) or Not now. Steed draws the bar, so
the page cannot press Allow itself.
"""

from PyQt6 import sip
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from browser.access_levels import PAGE_CAPABILITIES


class PageAccessBar(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self.owner = window
        self.setObjectName("PasswordBar")  # Same look as the other page bars.
        self.host = None
        self.wanted = []
        self.declined = set()  # (host, capability) passed over this session
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 8, 6)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self.allow = QPushButton("Allow", self)
        self.allow.setProperty("primary", True)
        self.allow.setAutoDefault(False)
        self.allow.clicked.connect(self.grant)
        row.addWidget(self.allow)
        self.later = QPushButton("Not now", self)
        self.later.setAutoDefault(False)
        self.later.clicked.connect(self.decline)
        row.addWidget(self.later)
        self.hide()

    def _front_host(self):
        tab = self.owner._current_browser_tab()
        if tab is None or sip.isdeleted(tab) or tab.url().scheme() != "steed":
            return None
        return tab.url().host()

    def offer(self, host, capability):
        """A page of yours was refused ``capability``; ask if it is in front."""
        if (host, capability) in self.declined or self._front_host() != host:
            return
        if host != self.host:
            self.host, self.wanted = host, []
        if capability not in self.wanted:
            self.wanted.append(capability)
        what = " and ".join(PAGE_CAPABILITIES[c]["label"].lower() for c in self.wanted)
        self.label.setText(
            f"steed:{host} wants to {what}. Allow it for this page? "
            "You can take it back in View → Your Pages."
        )
        self.show()

    def grant(self):
        manager = self.owner.profile_manager
        pages = manager.registry.start_pages
        for capability in self.wanted:
            pages.allow_page(manager.current_profile_name, self.host, capability)
        host, self.host, self.wanted = self.host, None, []
        self.hide()
        tab = self.owner._current_browser_tab()
        if tab is not None and tab.url().host() == host:
            tab.reload()  # The page asks again, and now gets an answer.

    def decline(self):
        self.declined.update((self.host, c) for c in self.wanted)
        self.host, self.wanted = None, []
        self.hide()

    def update_for(self, url: QUrl):
        """The page in front changed: the bar belongs to one page."""
        if self.host is not None and QUrl(url).host() != self.host:
            self.host, self.wanted = None, []
            self.hide()
