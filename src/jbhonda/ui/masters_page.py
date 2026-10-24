"""Vehicle models, financers and insurers: the lists other screens pick from."""

import sqlite3
from collections.abc import Callable, Sequence

from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import VEHICLE_CATEGORIES, save_vehicle_model
from jbhonda.core.masters import save_financer, save_insurer, set_active
from jbhonda.core.money import format_inr
from jbhonda.ui import widgets
from jbhonda.ui.widgets import ChoiceCombo, Column, DataTable, FormDialog, GstRateCombo, MoneyEdit


def _active_label(row) -> str:
    return "Active" if row["is_active"] else "Inactive"


# ---------------------------------------------------------------- dialogs

class VehicleModelDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, model_id: int | None = None, parent: QWidget | None = None):
        super().__init__("Edit vehicle model" if model_id else "Add vehicle model", parent)
        self.conn, self.model_id = conn, model_id
        self.model_name = QLineEdit()
        self.variant = QLineEdit()
        self.variant.setPlaceholderText("e.g. STD, DLX, Disc")
        self.category = ChoiceCombo([(c.title(), c) for c in VEHICLE_CATEGORIES])
        self.engine_cc = QSpinBox()
        self.engine_cc.setRange(0, 2000)
        self.engine_cc.setSpecialValueText("-")
        self.hsn_code = QLineEdit("8711")
        self.gst_rate = GstRateCombo()
        self.gst_rate.setValue(1800)
        self.price = MoneyEdit()
        self.form.addRow("Model name *", self.model_name)
        self.form.addRow("Variant", self.variant)
        self.form.addRow("Category *", self.category)
        self.form.addRow("Engine CC", self.engine_cc)
        self.form.addRow("HSN code *", self.hsn_code)
        self.form.addRow("GST rate *", self.gst_rate)
        self.form.addRow("Ex-showroom price (incl. GST) *", self.price)
        if model_id:
            row = conn.execute("SELECT * FROM vehicle_model WHERE id = ?", (model_id,)).fetchone()
            self.model_name.setText(row["model_name"])
            self.variant.setText(row["variant"])
            self.category.setValue(row["category"])
            self.engine_cc.setValue(row["engine_cc"] or 0)
            self.hsn_code.setText(row["hsn_code"])
            self.gst_rate.setValue(row["gst_rate_bp"])
            self.price.setValue(row["ex_showroom_price"])

    def save(self) -> None:
        price = self.price.value()
        if price is None:
            raise BusinessError("Enter the ex-showroom price.")
        save_vehicle_model(
            self.conn, model_id=self.model_id, model_name=self.model_name.text(), variant=self.variant.text(),
            category=self.category.value(), engine_cc=self.engine_cc.value() or None,
            hsn_code=self.hsn_code.text(), gst_rate_bp=self.gst_rate.value(), ex_showroom_price=price,
        )


class FinancerDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, financer_id: int | None = None, parent: QWidget | None = None):
        super().__init__("Edit financer" if financer_id else "Add financer", parent)
        self.conn, self.financer_id = conn, financer_id
        self.name, self.branch, self.contact, self.phone = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.name.setPlaceholderText("e.g. HDFC Bank, Honda Finance")
        self.form.addRow("Name *", self.name)
        self.form.addRow("Branch", self.branch)
        self.form.addRow("Contact person", self.contact)
        self.form.addRow("Phone", self.phone)
        if financer_id:
            row = conn.execute("SELECT * FROM financer WHERE id = ?", (financer_id,)).fetchone()
            self.name.setText(row["name"])
            self.branch.setText(row["branch"])
            self.contact.setText(row["contact_person"] or "")
            self.phone.setText(row["phone"] or "")

    def save(self) -> None:
        save_financer(
            self.conn, financer_id=self.financer_id, name=self.name.text(), branch=self.branch.text(),
            contact_person=self.contact.text(), phone=self.phone.text(),
        )


class InsurerDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, insurer_id: int | None = None, parent: QWidget | None = None):
        super().__init__("Edit insurance company" if insurer_id else "Add insurance company", parent)
        self.conn, self.insurer_id = conn, insurer_id
        self.name, self.contact, self.phone = QLineEdit(), QLineEdit(), QLineEdit()
        self.form.addRow("Company name *", self.name)
        self.form.addRow("Contact person", self.contact)
        self.form.addRow("Phone", self.phone)
        if insurer_id:
            row = conn.execute("SELECT * FROM insurer WHERE id = ?", (insurer_id,)).fetchone()
            self.name.setText(row["name"])
            self.contact.setText(row["contact_person"] or "")
            self.phone.setText(row["phone"] or "")

    def save(self) -> None:
        save_insurer(
            self.conn, insurer_id=self.insurer_id, name=self.name.text(),
            contact_person=self.contact.text(), phone=self.phone.text(),
        )


# ---------------------------------------------------------------- list tabs

class MasterTab(QWidget):
    """A list with Add / Edit / Activate-Deactivate buttons."""

    def __init__(
        self, conn: sqlite3.Connection, *, table: str, noun: str, columns: Sequence[Column], order_by: str,
        dialog: Callable[..., FormDialog], parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.conn, self.table_name, self.noun, self.order_by, self.dialog = conn, table, noun, order_by, dialog
        self.table = DataTable([*columns, Column("Status", _active_label)])
        self.table.doubleClicked.connect(self.edit)
        self.table.itemSelectionChanged.connect(self._update_buttons)

        self.add_button = QPushButton(f"Add {noun}")
        self.add_button.setObjectName("primary")
        self.edit_button = QPushButton("Edit")
        self.toggle_button = QPushButton("Deactivate")
        self.show_inactive = QCheckBox("Show inactive")
        self.add_button.clicked.connect(self.add)
        self.edit_button.clicked.connect(self.edit)
        self.toggle_button.clicked.connect(self.toggle_active)
        self.show_inactive.toggled.connect(self.refresh)

        buttons = QHBoxLayout()
        for w in (self.add_button, self.edit_button, self.toggle_button):
            buttons.addWidget(w)
        buttons.addStretch()
        buttons.addWidget(self.show_inactive)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.table)
        self.refresh()

    def rows(self) -> list[sqlite3.Row]:
        where = "" if self.show_inactive.isChecked() else "WHERE is_active = 1"
        return self.conn.execute(f"SELECT * FROM {self.table_name} {where} ORDER BY {self.order_by}").fetchall()

    def refresh(self) -> None:
        self.table.fill(self.rows())
        self._update_buttons()

    def _selected_row(self) -> sqlite3.Row | None:
        record_id = self.table.selected_id()
        if record_id is None:
            return None
        return self.conn.execute(f"SELECT * FROM {self.table_name} WHERE id = ?", (record_id,)).fetchone()

    def _update_buttons(self) -> None:
        row = self._selected_row()
        self.edit_button.setEnabled(row is not None)
        self.toggle_button.setEnabled(row is not None)
        self.toggle_button.setText("Activate" if row is not None and not row["is_active"] else "Deactivate")

    def add(self) -> None:
        if self.dialog(self.conn, None, self).exec():
            self.refresh()

    def edit(self) -> None:
        row = self._selected_row()
        if row is not None and self.dialog(self.conn, row["id"], self).exec():
            self.refresh()

    def toggle_active(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        activate = not row["is_active"]
        if not activate and not widgets.confirm(
            self, f"Deactivate this {self.noun}? It will no longer appear in pick lists. Old records are kept."
        ):
            return
        set_active(self.conn, self.table_name, row["id"], activate)
        self.refresh()


class MastersPage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        title = QLabel("Masters")
        title.setObjectName("pageTitle")
        self.models = MasterTab(
            conn, table="vehicle_model", noun="model", order_by="model_name, variant", dialog=VehicleModelDialog,
            columns=[
                Column("Model", lambda r: r["model_name"]),
                Column("Variant", lambda r: r["variant"]),
                Column("Category", lambda r: r["category"].title()),
                Column("CC", lambda r: r["engine_cc"], align_right=True),
                Column("HSN", lambda r: r["hsn_code"]),
                Column("GST", lambda r: f"{r['gst_rate_bp'] / 100:g}%", align_right=True),
                Column("Ex-showroom price", lambda r: format_inr(r["ex_showroom_price"]), align_right=True),
            ],
        )
        self.financers = MasterTab(
            conn, table="financer", noun="financer", order_by="name, branch", dialog=FinancerDialog,
            columns=[
                Column("Name", lambda r: r["name"]),
                Column("Branch", lambda r: r["branch"]),
                Column("Contact person", lambda r: r["contact_person"]),
                Column("Phone", lambda r: r["phone"]),
            ],
        )
        self.insurers = MasterTab(
            conn, table="insurer", noun="insurance company", order_by="name", dialog=InsurerDialog,
            columns=[
                Column("Company", lambda r: r["name"]),
                Column("Contact person", lambda r: r["contact_person"]),
                Column("Phone", lambda r: r["phone"]),
            ],
        )
        tabs = QTabWidget()
        tabs.addTab(self.models, "Vehicle models")
        tabs.addTab(self.financers, "Financers")
        tabs.addTab(self.insurers, "Insurance companies")
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(tabs)
