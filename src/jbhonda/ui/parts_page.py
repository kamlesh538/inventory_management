"""Parts stock: part master, stock-in from purchases, adjustments, and per-part history."""

import sqlite3

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import (
    PART_CATEGORIES, PART_UNITS, adjust_stock, find_parts, part_history, purchase_part, save_part,
)
from jbhonda.core.masters import set_active
from jbhonda.core.money import div_round, format_inr
from jbhonda.ui import widgets
from jbhonda.ui.widgets import ChoiceCombo, Column, DataTable, FormDialog, GstRateCombo, MoneyEdit, format_date

MOVEMENT_LABELS = {
    "PURCHASE": "Purchase",
    "SALE": "Sale",
    "SALE_CANCEL": "Sale cancelled",
    "ADJUSTMENT": "Adjustment",
    "RETURN_TO_SUPPLIER": "Returned to supplier",
}
ADJUST_REASONS = ("Physical count correction", "Damaged", "Lost / missing", "Used in workshop", "Found in stock")


def part_label(part) -> str:
    return f"{part['part_number']} - {part['name']}"


def _stock_text(part) -> str:
    return f"{part['stock_qty']} {part['unit'].lower()}"


class PartDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, part_id: int | None = None, parent: QWidget | None = None):
        super().__init__("Edit part" if part_id else "Add part", parent)
        self.conn, self.part_id = conn, part_id
        self.part_number = QLineEdit()
        self.name = QLineEdit()
        self.category = ChoiceCombo([(c.title(), c) for c in PART_CATEGORIES])
        self.unit = ChoiceCombo([(u.title(), u) for u in PART_UNITS])
        self.hsn_code = QLineEdit("8714")
        self.gst_rate = GstRateCombo()
        self.gst_rate.setValue(1800)
        self.mrp = MoneyEdit()
        self.sale_price = MoneyEdit()
        self.reorder_level = QSpinBox()
        self.reorder_level.setRange(0, 100000)
        self.reorder_level.setToolTip("The part is flagged as low on stock at or below this quantity")
        self.rack = QLineEdit()
        self.rack.setPlaceholderText("e.g. A-3")
        # Sale price follows MRP until the user types a sale price of their own.
        self._sale_price_typed = False
        self.sale_price.textEdited.connect(lambda: setattr(self, "_sale_price_typed", True))
        self.mrp.textEdited.connect(self._default_sale_price)

        self.form.addRow("Part number *", self.part_number)
        self.form.addRow("Name *", self.name)
        self.form.addRow("Category", self.category)
        self.form.addRow("Unit", self.unit)
        self.form.addRow("HSN code *", self.hsn_code)
        self.form.addRow("GST rate *", self.gst_rate)
        self.form.addRow("MRP", self.mrp)
        self.form.addRow("Sale price (incl. GST) *", self.sale_price)
        self.form.addRow("Reorder level", self.reorder_level)
        self.form.addRow("Rack location", self.rack)

        if part_id:
            p = conn.execute("SELECT * FROM part WHERE id = ?", (part_id,)).fetchone()
            self.part_number.setText(p["part_number"])
            self.name.setText(p["name"])
            self.category.setValue(p["category"])
            self.unit.setValue(p["unit"])
            self.hsn_code.setText(p["hsn_code"])
            self.gst_rate.setValue(p["gst_rate_bp"])
            self.mrp.setValue(p["mrp"] or None)
            self.sale_price.setValue(p["sale_price"])
            self._sale_price_typed = True
            self.reorder_level.setValue(p["reorder_level"])
            self.rack.setText(p["rack_location"] or "")
            self.form.addRow("Current stock", QLabel(f"{_stock_text(p)}  (change it with Stock in / Adjust stock)"))
        else:
            self.opening_qty = QSpinBox()
            self.opening_qty.setRange(0, 100000)
            self.opening_cost = MoneyEdit()
            self.form.addRow("Opening stock", self.opening_qty)
            self.form.addRow("Cost per unit (excl. GST)", self.opening_cost)

    def _default_sale_price(self) -> None:
        if not self._sale_price_typed:
            self.sale_price.setText(self.mrp.text())

    def save(self) -> None:
        sale_price = self.sale_price.value()
        if sale_price is None:
            raise BusinessError("Enter the sale price.")
        extra = {}
        if self.part_id is None:
            extra = {"opening_qty": self.opening_qty.value(), "opening_unit_cost": self.opening_cost.value() or 0}
        self.part_id = save_part(
            self.conn, part_id=self.part_id, part_number=self.part_number.text(), name=self.name.text(),
            category=self.category.value(), unit=self.unit.value(), hsn_code=self.hsn_code.text(),
            gst_rate_bp=self.gst_rate.value(), mrp=self.mrp.value() or 0, sale_price=sale_price,
            reorder_level=self.reorder_level.value(), rack_location=self.rack.text(), **extra,
        )


class StockInDialog(FormDialog):
    """Record parts received from a supplier bill."""

    def __init__(self, conn: sqlite3.Connection, part_id: int, parent: QWidget | None = None):
        super().__init__("Stock in", parent)
        self.conn, self.part_id = conn, part_id
        self.part = conn.execute("SELECT * FROM part WHERE id = ?", (part_id,)).fetchone()
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 100000)
        self.unit_cost = MoneyEdit()
        if self.part["avg_cost"]:
            self.unit_cost.setValue(self.part["avg_cost"])
        self.gst = MoneyEdit()
        self.bill_no = QLineEdit()
        self.bill_no.setPlaceholderText("Supplier bill / invoice number")
        self.date = QDateEdit(QDate.currentDate())
        self.date.setDisplayFormat("dd-MM-yyyy")
        self.date.setCalendarPopup(True)
        self.date.setMaximumDate(QDate.currentDate())
        self.quantity.valueChanged.connect(self._fill_gst)
        self.unit_cost.textEdited.connect(self._fill_gst)

        self.form.addRow("Part", QLabel(part_label(self.part)))
        self.form.addRow("In stock now", QLabel(_stock_text(self.part)))
        self.form.addRow("Quantity received *", self.quantity)
        self.form.addRow("Cost per unit (excl. GST) *", self.unit_cost)
        self.form.addRow("GST paid (total)", self.gst)
        self.form.addRow("Supplier bill no.", self.bill_no)
        self.form.addRow("Date", self.date)
        self._fill_gst()

    def _fill_gst(self) -> None:
        try:
            cost = self.unit_cost.value()
        except ValueError:
            return
        if cost is not None:
            self.gst.setValue(div_round(cost * self.quantity.value() * self.part["gst_rate_bp"], 10000))

    def save(self) -> None:
        cost = self.unit_cost.value()
        if cost is None:
            raise BusinessError("Enter the cost per unit.")
        purchase_part(
            self.conn, part_id=self.part_id, quantity=self.quantity.value(), unit_cost=cost,
            gst_amount=self.gst.value() or 0, reference_no=self.bill_no.text().strip().upper() or None,
            on=self.date.date().toPython(),
        )


class AdjustStockDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, part_id: int, parent: QWidget | None = None):
        super().__init__("Adjust stock", parent)
        self.conn, self.part_id = conn, part_id
        part = conn.execute("SELECT * FROM part WHERE id = ?", (part_id,)).fetchone()
        self.change = QSpinBox()
        self.change.setRange(-part["stock_qty"], 100000)
        self.change.setValue(0)
        self.reason = QComboBox()
        self.reason.setEditable(True)
        self.reason.addItems(ADJUST_REASONS)
        self.reason.setCurrentText("")
        self.result_label = QLabel()
        self.change.valueChanged.connect(
            lambda v: self.result_label.setText(f"{part['stock_qty'] + v} {part['unit'].lower()}")
        )
        self.form.addRow("Part", QLabel(part_label(part)))
        self.form.addRow("In stock now", QLabel(_stock_text(part)))
        self.form.addRow("Add (+) / remove (-) *", self.change)
        self.form.addRow("Stock after", self.result_label)
        self.form.addRow("Reason *", self.reason)
        self.change.valueChanged.emit(0)

    def save(self) -> None:
        adjust_stock(self.conn, part_id=self.part_id, quantity_change=self.change.value(),
                     reason=self.reason.currentText())


class PartHistoryDialog(QDialog):
    def __init__(self, conn: sqlite3.Connection, part_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        part = conn.execute("SELECT * FROM part WHERE id = ?", (part_id,)).fetchone()
        self.setWindowTitle(f"Stock history - {part_label(part)}")
        self.resize(900, 500)
        self.table = DataTable([
            Column("Date", lambda r: format_date(r["movement_date"]), sort_key=lambda r: (r["movement_date"], r["id"])),
            Column("Type", lambda r: MOVEMENT_LABELS[r["movement_type"]]),
            Column("Qty", lambda r: f"{r['quantity']:+d}", align_right=True, sort_key=lambda r: r["quantity"]),
            Column("Unit cost", lambda r: format_inr(r["unit_cost"]), align_right=True, sort_key=lambda r: r["unit_cost"]),
            Column("Bill / invoice", lambda r: r["invoice_no"] or r["reference_no"]),
            Column("Customer", lambda r: r["bill_name"]),
            Column("Balance", lambda r: r["balance"], align_right=True, sort_key=lambda r: r["balance"]),
            Column("Notes", lambda r: r["notes"]),
        ])
        self.table.fill(part_history(conn, part_id))
        self.table.scrollToBottom()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(close)


class PartsPage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        title = QLabel("Parts")
        title.setObjectName("pageTitle")

        self.add_button = QPushButton("Add part")
        self.add_button.setObjectName("primary")
        self.edit_button = QPushButton("Edit")
        self.stock_in_button = QPushButton("Stock in")
        self.adjust_button = QPushButton("Adjust stock")
        self.history_button = QPushButton("History")
        self.toggle_button = QPushButton("Deactivate")
        self.selection_buttons = (
            self.edit_button, self.stock_in_button, self.adjust_button, self.history_button, self.toggle_button,
        )
        self.add_button.clicked.connect(self.add)
        self.edit_button.clicked.connect(lambda: self._open(PartDialog))
        self.stock_in_button.clicked.connect(lambda: self._open(StockInDialog))
        self.adjust_button.clicked.connect(lambda: self._open(AdjustStockDialog))
        self.history_button.clicked.connect(self.show_history)
        self.toggle_button.clicked.connect(self.toggle_active)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search part number, name or rack")
        self.search.setClearButtonEnabled(True)
        self.category_filter = ChoiceCombo([("All categories", None), *((c.title(), c) for c in PART_CATEGORIES)])
        self.low_only = QCheckBox("Low stock only")
        self.show_inactive = QCheckBox("Show inactive")
        self.search.textChanged.connect(self.refresh)
        self.category_filter.currentIndexChanged.connect(self.refresh)
        self.low_only.toggled.connect(self.refresh)
        self.show_inactive.toggled.connect(self.refresh)

        self.table = DataTable([
            Column("Part no.", lambda r: r["part_number"]),
            Column("Name", lambda r: r["name"] + ("" if r["is_active"] else "  (inactive)")),
            Column("Category", lambda r: r["category"].title()),
            Column("Rack", lambda r: r["rack_location"]),
            Column("Stock", _stock_text, align_right=True, sort_key=lambda r: r["stock_qty"]),
            Column("Reorder at", lambda r: r["reorder_level"], align_right=True, sort_key=lambda r: r["reorder_level"]),
            Column("MRP", lambda r: format_inr(r["mrp"]) if r["mrp"] else "", align_right=True,
                   sort_key=lambda r: r["mrp"]),
            Column("Sale price", lambda r: format_inr(r["sale_price"]), align_right=True,
                   sort_key=lambda r: r["sale_price"]),
            Column("GST", lambda r: f"{r['gst_rate_bp'] / 100:g}%", align_right=True, sort_key=lambda r: r["gst_rate_bp"]),
            Column("Avg. cost", lambda r: format_inr(r["avg_cost"]), align_right=True, sort_key=lambda r: r["avg_cost"]),
            Column("Stock value", lambda r: format_inr(r["stock_qty"] * r["avg_cost"]), align_right=True,
                   sort_key=lambda r: r["stock_qty"] * r["avg_cost"]),
        ], highlight=lambda r: bool(r["is_low"]) and bool(r["is_active"]))
        self.table.doubleClicked.connect(lambda: self._open(PartDialog))
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.footer = QLabel()

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        for b in self.selection_buttons:
            buttons.addWidget(b)
        buttons.addStretch()
        filters = QHBoxLayout()
        filters.addWidget(self.search, 3)
        filters.addWidget(self.category_filter, 1)
        filters.addWidget(self.low_only)
        filters.addWidget(self.show_inactive)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(buttons)
        layout.addLayout(filters)
        layout.addWidget(self.table)
        layout.addWidget(self.footer)
        self.refresh()

    def refresh(self) -> None:
        rows = find_parts(
            self.conn, text=self.search.text(), category=self.category_filter.value(),
            low_stock_only=self.low_only.isChecked(), include_inactive=self.show_inactive.isChecked(),
        )
        self.table.fill(rows)
        self._update_buttons()
        low = sum(1 for r in rows if r["is_low"] and r["is_active"])
        value = sum(r["stock_qty"] * r["avg_cost"] for r in rows)
        self.footer.setText(f"{len(rows)} parts shown  ·  {low} low on stock  ·  stock value at cost {format_inr(value)}")

    def _selected(self) -> sqlite3.Row | None:
        part_id = self.table.selected_id()
        return None if part_id is None else self.conn.execute("SELECT * FROM part WHERE id = ?", (part_id,)).fetchone()

    def _update_buttons(self) -> None:
        part = self._selected()
        for b in self.selection_buttons:
            b.setEnabled(part is not None)
        if part is not None:
            self.toggle_button.setText("Activate" if not part["is_active"] else "Deactivate")
            self.adjust_button.setEnabled(bool(part["is_active"]))
            self.stock_in_button.setEnabled(bool(part["is_active"]))

    def _reselect(self, part_id: int) -> None:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == part_id:
                self.table.selectRow(r)
                return

    def add(self) -> None:
        dialog = PartDialog(self.conn, parent=self)
        if dialog.exec():
            self.refresh()
            self._reselect(dialog.part_id)

    def _open(self, dialog_class) -> None:
        part = self._selected()
        if part is not None and dialog_class(self.conn, part["id"], self).exec():
            self.refresh()
            self._reselect(part["id"])

    def show_history(self) -> None:
        part = self._selected()
        if part is not None:
            PartHistoryDialog(self.conn, part["id"], self).exec()

    def toggle_active(self) -> None:
        part = self._selected()
        if part is None:
            return
        if part["is_active"] and not widgets.confirm(
            self, f"Deactivate {part['part_number']}? It will no longer appear when billing. History is kept."
        ):
            return
        set_active(self.conn, "part", part["id"], not part["is_active"])
        self.refresh()

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)
