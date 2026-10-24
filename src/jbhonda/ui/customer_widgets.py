"""Customer add/edit dialog and a search-and-pick widget used by the sales screens."""

import sqlite3

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from jbhonda.core.customers import (
    ID_PROOF_TYPES, OPTIONAL_FIELDS, RELATION_TYPES, customer_label, save_customer, search_customers,
)
from jbhonda.core.settings import get_settings
from jbhonda.ui.widgets import ChoiceCombo, FormDialog

_TEXT_FIELDS = (
    ("relation_name", "Father's / husband's name"),
    ("alt_phone", "Alternate phone"),
    ("address_line1", "Address line 1"),
    ("address_line2", "Address line 2"),
    ("city", "City / village"),
    ("district", "District"),
    ("state", "State"),
    ("pincode", "PIN code"),
    ("email", "Email"),
    ("gstin", "GSTIN (business buyers)"),
    ("id_proof_number", "ID proof number"),
)


class CustomerDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, customer_id: int | None = None, parent: QWidget | None = None,
                 initial_text: str = ""):
        super().__init__("Edit customer" if customer_id else "New customer", parent)
        self.setMinimumWidth(480)
        self.conn, self.customer_id = conn, customer_id
        self.name = QLineEdit()
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("10-digit mobile")
        self.relation_type = ChoiceCombo([("", None), *((r, r) for r in RELATION_TYPES)])
        self.id_proof_type = ChoiceCombo([("", None), *((t.replace("_", " ").title(), t) for t in ID_PROOF_TYPES)])
        self.inputs = {key: QLineEdit() for key, _ in _TEXT_FIELDS}

        self.form.addRow("Name *", self.name)
        self.form.addRow("Mobile *", self.phone)
        self.form.addRow("Relation", self.relation_type)
        for key, label in _TEXT_FIELDS:
            if key == "id_proof_number":
                self.form.addRow("ID proof type", self.id_proof_type)
            self.form.addRow(label, self.inputs[key])

        if customer_id:
            c = conn.execute("SELECT * FROM customer WHERE id = ?", (customer_id,)).fetchone()
            self.name.setText(c["name"])
            self.phone.setText(c["phone"])
            self.relation_type.setValue(c["relation_type"])
            self.id_proof_type.setValue(c["id_proof_type"])
            for key, edit in self.inputs.items():
                edit.setText(c[key] or "")
        else:
            self.inputs["state"].setText(get_settings(conn)["state"] or "")
            # Whatever was typed in the search box is probably the phone or the name.
            text = initial_text.strip()
            (self.phone if text.replace(" ", "").isdigit() else self.name).setText(text)

    def save(self) -> None:
        details = {key: edit.text() for key, edit in self.inputs.items()}
        details["relation_type"] = self.relation_type.value()
        details["id_proof_type"] = self.id_proof_type.value()
        assert set(details) <= set(OPTIONAL_FIELDS)
        self.customer_id = save_customer(
            self.conn, customer_id=self.customer_id, name=self.name.text(), phone=self.phone.text(), **details,
        )


class CustomerPicker(QWidget):
    """Search by name or phone, pick from matches, or add / edit a customer."""

    changed = Signal(object)  # customer id or None

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type name or mobile number")
        self.search.setClearButtonEnabled(True)
        self.results = QComboBox()
        self.new_button = QPushButton("New customer")
        self.edit_button = QPushButton("Edit")
        self.search.textChanged.connect(self._search)
        self.results.currentIndexChanged.connect(self._picked)
        self.new_button.clicked.connect(self.new_customer)
        self.edit_button.clicked.connect(self.edit_customer)

        row = QHBoxLayout()
        row.addWidget(self.search, 2)
        row.addWidget(self.new_button)
        row2 = QHBoxLayout()
        row2.addWidget(self.results, 2)
        row2.addWidget(self.edit_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addLayout(row2)
        self._last: int | None = None
        self._search()

    def customer_id(self) -> int | None:
        return self.results.currentData()

    def _fill(self, rows, keep: int | None) -> None:
        self.results.blockSignals(True)
        self.results.clear()
        self.results.addItem("- choose a customer -", None)
        for c in rows:
            self.results.addItem(customer_label(c), c["id"])
        index = self.results.findData(keep) if keep is not None else -1
        if index < 0 and len(rows) == 1 and self.search.text().strip():
            index = 1  # a single match is almost certainly the one being typed
        self.results.setCurrentIndex(max(index, 0))
        self.results.blockSignals(False)
        self._picked()

    def _search(self) -> None:
        self._fill(search_customers(self.conn, self.search.text()), keep=self.customer_id())

    def _picked(self) -> None:
        current = self.customer_id()
        self.edit_button.setEnabled(current is not None)
        if current != self._last:
            self._last = current
            self.changed.emit(current)

    def set_customer(self, customer_id: int | None) -> None:
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        rows = search_customers(self.conn, "")
        if customer_id is not None and all(r["id"] != customer_id for r in rows):
            rows = [self.conn.execute("SELECT * FROM customer WHERE id = ?", (customer_id,)).fetchone(), *rows]
        self._fill(rows, keep=customer_id)

    def new_customer(self) -> None:
        dialog = CustomerDialog(self.conn, parent=self, initial_text=self.search.text())
        if dialog.exec():
            self.set_customer(dialog.customer_id)

    def edit_customer(self) -> None:
        current = self.customer_id()
        if current is not None and CustomerDialog(self.conn, current, self).exec():
            self.set_customer(current)
            self.changed.emit(current)  # details changed; let the screen refresh what it shows
