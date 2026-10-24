import sqlite3

from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from jbhonda.core.errors import BusinessError
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
        column.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        QVBoxLayout(self).addWidget(scroll)
        self.load()

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
