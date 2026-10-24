import sqlite3
import time

from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from jbhonda import config
from jbhonda.core.errors import BusinessError
from jbhonda.db.schema import backup_database
from jbhonda.core.settings import get_settings, update_settings
from jbhonda.ui import widgets

_GROUPS = (
    ("Shop", (("shop_name", "Shop name *"), ("phone", "Phone"), ("email", "Email"))),
    ("Address", (
        ("address_line1", "Address line 1"), ("address_line2", "Address line 2"), ("city", "City"),
        ("district", "District"), ("state", "State"), ("pincode", "PIN code"),
    )),
    ("Tax", (("gstin", "GSTIN"), ("pan", "PAN"), ("state_code", "State code"))),
    ("Bank details (printed on invoices)", (
        ("bank_name", "Bank name"), ("bank_account_no", "Account number"), ("bank_ifsc", "IFSC"),
    )),
)


class SettingsPage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self.inputs: dict[str, QLineEdit] = {}

        content = QWidget()
        content.setMaximumWidth(780)
        column = QVBoxLayout(content)
        title = QLabel("Shop settings")
        title.setObjectName("pageTitle")
        column.addWidget(title)
        for group_title, fields in _GROUPS:
            box = QGroupBox(group_title)
            form = QFormLayout(box)
            for key, label in fields:
                self.inputs[key] = QLineEdit()
                form.addRow(label, self.inputs[key])
            column.addWidget(box)
        self.inputs["state_code"].setReadOnly(True)
        self.inputs["state_code"].setToolTip("Filled automatically from the GSTIN")

        terms_box = QGroupBox("Terms && conditions (printed at the bottom of invoices)")
        self.terms = QPlainTextEdit()
        self.terms.setFixedHeight(90)
        QVBoxLayout(terms_box).addWidget(self.terms)
        column.addWidget(terms_box)

        self.save_button = QPushButton("Save settings")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.save_button)
        column.addLayout(row)
        column.addWidget(self._practice_box())
        column.addWidget(self._erase_box())
        column.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        QVBoxLayout(self).addWidget(scroll)
        self.load()

    # ------------------------------------------------------------ practice mode & erase

    def _practice_box(self) -> QGroupBox:
        box = QGroupBox("Practice mode")
        v = QVBoxLayout(box)
        if config.practice_mode():
            text = ("You are in PRACTICE MODE. Sales, stock and invoices here are separate from your real data "
                    "and can be thrown away.")
            self.practice_button = QPushButton("Switch back to real data")
            self.practice_button.clicked.connect(lambda: self.switch_practice_mode(False))
            self.delete_practice_button = None
        else:
            text = ("Try the app with dummy vehicles, parts and invoices without touching real data or real "
                    "invoice numbers. Practice data is kept separately until you delete it.")
            self.practice_button = QPushButton("Switch to practice mode")
            self.practice_button.clicked.connect(lambda: self.switch_practice_mode(True))
            self.delete_practice_button = QPushButton("Delete practice data")
            self.delete_practice_button.setEnabled(config.practice_dir().exists())
            self.delete_practice_button.clicked.connect(self.delete_practice_data)
        label = QLabel(text)
        label.setWordWrap(True)
        v.addWidget(label)
        row = QHBoxLayout()
        row.addWidget(self.practice_button)
        if self.delete_practice_button is not None:
            row.addWidget(self.delete_practice_button)
        row.addStretch()
        v.addLayout(row)
        return box

    def _erase_box(self) -> QGroupBox:
        which = "practice" if config.practice_mode() else "ALL"
        box = QGroupBox("Start fresh")
        v = QVBoxLayout(box)
        label = QLabel(f"Erase {which} data (vehicles, parts, customers, invoices, settings) and start with an "
                       "empty system - for example after testing, before going live. A backup is taken first.")
        label.setWordWrap(True)
        self.erase_button = QPushButton(f"Erase {which.lower()} data...")
        self.erase_button.clicked.connect(self.erase_all)
        v.addWidget(label)
        row = QHBoxLayout()
        row.addWidget(self.erase_button)
        row.addStretch()
        v.addLayout(row)
        return box

    def switch_practice_mode(self, on: bool) -> None:
        message = ("Switch to PRACTICE MODE? The app will restart with separate practice data."
                   if on else "Switch back to your REAL data? The app will restart.")
        if not widgets.confirm(self, message):
            return
        config.set_practice_mode(on)
        _app().restart_app()

    def delete_practice_data(self) -> None:
        if not widgets.confirm(self, "Delete all practice data? Your real data is not affected."):
            return
        config.delete_practice_data()
        self.delete_practice_button.setEnabled(False)
        widgets.show_info(self, "Practice data deleted.")

    def erase_all(self) -> bool:
        typed = widgets.ask_text(self, "Erase data", "This removes everything. Type ERASE to confirm:")
        if (typed or "").strip().upper() != "ERASE":
            return False
        backup = backup_database(self.conn, config.backup_dir(), label="before-erase")
        self.conn.close()
        db = config.db_path()
        for suffix in ("", "-wal", "-shm"):
            path = db.with_name(db.name + suffix)
            for _ in range(10):  # Windows may hold the file a moment after closing
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.2)
        widgets.show_info(self, f"All data erased. A backup was saved as:\n{backup}\n\nThe app will now restart.")
        _app().restart_app()
        return True

    def load(self) -> None:
        settings = get_settings(self.conn)
        for key, edit in self.inputs.items():
            edit.setText(settings[key] or "")
        self.terms.setPlainText(settings["invoice_terms"] or "")

    def save(self) -> bool:
        values = {k: e.text() for k, e in self.inputs.items() if k != "state_code"}
        values["invoice_terms"] = self.terms.toPlainText()
        try:
            update_settings(self.conn, **values)
        except BusinessError as exc:
            widgets.show_error(self, str(exc))
            return False
        self.load()
        widgets.show_info(self, "Settings saved.")
        return True

    def showEvent(self, event) -> None:
        self.load()
        super().showEvent(event)


def _app():
    from jbhonda.ui import app  # imported late: app.py builds the main window that contains this page

    return app
