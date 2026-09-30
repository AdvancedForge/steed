"""Cookie viewer/editor dialog."""

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from cookie_manager import CookieInfo


class CookieDialog(QDialog):
    """Dialog for viewing and managing cookies."""

    def __init__(self, cookies: list[CookieInfo], parent=None):
        super().__init__(parent)
        self.cookies = cookies
        self.setup_ui()
        self.populate_table()

    def setup_ui(self) -> None:
        """Set up the UI."""
        self.setWindowTitle("Cookies")
        self.setMinimumSize(800, 600)

        layout = QVBoxLayout(self)

        # Filter
        filter_layout = QHBoxLayout()
        filter_label = QLabel("Filter:")
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter by domain or name...")
        self.filter_input.textChanged.connect(self.filter_cookies)
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(self.filter_input)
        layout.addLayout(filter_layout)

        # Cookie table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["Domain", "Name", "Value", "Path", "Expires", "Secure"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()

        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self.delete_selected)

        self.clear_all_btn = QPushButton("Clear All")
        self.clear_all_btn.clicked.connect(self.clear_all)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)

        button_layout.addWidget(self.delete_btn)
        button_layout.addWidget(self.clear_all_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.close_btn)
        layout.addLayout(button_layout)

    def populate_table(self, cookies: list[CookieInfo] | None = None) -> None:
        """Populate the table with cookies."""
        if cookies is None:
            cookies = self.cookies

        self.table.setRowCount(len(cookies))

        for row, cookie_info in enumerate(cookies):
            self.table.setItem(row, 0, QTableWidgetItem(cookie_info.domain))
            self.table.setItem(row, 1, QTableWidgetItem(cookie_info.name))

            # Truncate long values
            value = cookie_info.value
            if len(value) > 50:
                value = value[:47] + "..."
            self.table.setItem(row, 2, QTableWidgetItem(value))

            self.table.setItem(row, 3, QTableWidgetItem(cookie_info.path))

            expires = (
                cookie_info.expiration_date.toString()
                if cookie_info.expiration_date.isValid()
                else "Session"
            )
            self.table.setItem(row, 4, QTableWidgetItem(expires))

            self.table.setItem(
                row, 5, QTableWidgetItem("Yes" if cookie_info.is_secure else "No")
            )

        self.table.resizeColumnsToContents()

    def filter_cookies(self, text: str) -> None:
        """Filter cookies by domain or name."""
        text = text.lower()
        if not text:
            self.populate_table()
            return

        filtered = [
            c
            for c in self.cookies
            if text in c.domain.lower() or text in c.name.lower()
        ]
        self.populate_table(filtered)

    def delete_selected(self) -> None:
        """Delete selected cookies."""
        selected_rows = set()
        for item in self.table.selectedItems():
            selected_rows.add(item.row())

        if not selected_rows:
            return

        reply = QMessageBox.question(
            self,
            "Delete Cookies",
            f"Delete {len(selected_rows)} cookie(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            # Emit signal or call callback
            self.cookies_to_delete = [
                self.cookies[row] for row in sorted(selected_rows, reverse=True)
            ]
            self.accept()

    def clear_all(self) -> None:
        """Clear all cookies."""
        reply = QMessageBox.question(
            self,
            "Clear All Cookies",
            "Delete all cookies?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.clear_all_requested = True
            self.accept()
