"""Counter sale of parts: scan or search parts, adjust qty/price/discount, take payment, bill."""

import sqlite3
from dataclasses import dataclass

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCompleter, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from jbhonda.core.errors import BusinessError
from jbhonda.core.gst import round_to_rupee
from jbhonda.core.inventory import find_parts, part_by_number
from jbhonda.core.money import format_inr
from jbhonda.core.sales import WALK_IN_NAME, PartLine, complete_part_sale
from jbhonda.core.validation import normalize_mobile
from jbhonda.ui import widgets
from jbhonda.ui.customer_widgets import CustomerPicker
from jbhonda.ui.payments import PaymentRow
from jbhonda.ui.widgets import MoneyEdit

COLUMNS = ("Part no.", "Name", "In stock", "Qty", "Price (incl. GST)", "Discount", "GST", "Amount", "")


def _lookup_label(part) -> str:
    return f"{part['part_number']}  -  {part['name']}  ({part['stock_qty']} in stock)"


@dataclass
class _BillLine:
    part: sqlite3.Row
    qty: QSpinBox
    price: MoneyEdit
    discount: MoneyEdit

    def amount(self) -> int:
        try:
            return max(self.qty.value() * (self.price.value() or 0) - (self.discount.value() or 0), 0)
        except ValueError:
            return 0


class SellPartsPage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self.lines: list[_BillLine] = []
        self.payment_rows: list[PaymentRow] = []
        self._label_to_part: dict[str, int] = {}

        title = QLabel("Sell parts")
        title.setObjectName("pageTitle")

        # -- customer
        self.customer = CustomerPicker(conn)
        self.walk_in_name = QLineEdit()
        self.walk_in_name.setPlaceholderText(WALK_IN_NAME)
        self.walk_in_phone = QLineEdit()
        self.walk_in_phone.setPlaceholderText("optional")
        customer_box = QGroupBox("Customer")
        f = QFormLayout(customer_box)
        f.addRow("Saved customer", self.customer)
        f.addRow("or walk-in name", self.walk_in_name)
        f.addRow("Walk-in mobile", self.walk_in_phone)
        self.customer.changed.connect(self._customer_changed)

        # -- part entry
        self.lookup = QLineEdit()
        self.lookup.setPlaceholderText("Scan barcode or type part number / name, then press Enter")
        self.lookup.setObjectName("lookup")
        self.completer_model = QStringListModel()
        completer = QCompleter(self.completer_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(12)
        completer.activated.connect(self._completer_picked)
        self.lookup.setCompleter(completer)
        self.lookup.returnPressed.connect(self._lookup_entered)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        # Input boxes in cells would otherwise claim far more width than they need.
        for column, width in ((3, 64), (4, 110), (5, 90), (8, 40)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        bill_box = QGroupBox("Items")
        v = QVBoxLayout(bill_box)
        v.addWidget(self.lookup)
        v.addWidget(self.table)

        # -- payment & totals
        payments_box = QGroupBox("Payment")
        self.payments_layout = QVBoxLayout(payments_box)
        add_payment = QPushButton("Add another payment")
        add_payment.clicked.connect(self.add_payment_row)
        self.payments_layout.addWidget(add_payment)

        totals_box = QGroupBox("Total")
        grid = QGridLayout(totals_box)
        self.total_labels: dict[str, QLabel] = {}
        for i, (key, text) in enumerate((("items", "Items"), ("total", "Bill total"), ("paying", "Paying now"),
                                         ("balance", "Balance due"))):
            value = QLabel()
            value.setObjectName("summaryTotal" if key == "total" else "summaryValue")
            grid.addWidget(QLabel(text), i, 0)
            grid.addWidget(value, i, 1)
            self.total_labels[key] = value
        self.create_button = QPushButton("Create bill  (F12)")
        self.create_button.setObjectName("primary")
        self.create_button.setMinimumHeight(40)
        clear_button = QPushButton("Clear")
        self.create_button.clicked.connect(self.create_bill)
        clear_button.clicked.connect(self.reset)
        QShortcut(QKeySequence("F12"), self, activated=self.create_bill)

        right = QVBoxLayout()
        right.addWidget(customer_box)
        right.addWidget(payments_box)
        right.addWidget(totals_box)
        buttons = QHBoxLayout()
        buttons.addWidget(clear_button)
        buttons.addWidget(self.create_button, 1)
        right.addLayout(buttons)
        right.addStretch()
        columns = QHBoxLayout()
        columns.addWidget(bill_box, 2)
        columns.addLayout(right, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(columns)

        self.add_payment_row()
        self.reload_parts()
        self.update_totals()

    # ------------------------------------------------------------ finding parts

    def reload_parts(self) -> None:
        parts = find_parts(self.conn)
        self._label_to_part = {_lookup_label(p): p["id"] for p in parts if p["stock_qty"] > 0}
        self.completer_model.setStringList(list(self._label_to_part))

    def _completer_picked(self, label: str) -> None:
        part_id = self._label_to_part.get(label)
        if part_id is not None:
            self.add_part(part_id)
            self.lookup.clear()

    def _lookup_entered(self) -> None:
        text = self.lookup.text().strip()
        if not text or text in self._label_to_part:
            return  # completer selection is handled by _completer_picked
        part = part_by_number(self.conn, text)
        if part is None:
            matches = [p for p in find_parts(self.conn, text=text) if p["stock_qty"] > 0]
            if len(matches) != 1:
                widgets.show_error(self, f"No single part matches '{text}'. Pick one from the list as you type.")
                return
            part = matches[0]
        self.add_part(part["id"])
        self.lookup.clear()

    # ------------------------------------------------------------ bill lines

    def add_part(self, part_id: int) -> None:
        """Add one of a part, or one more if it's already on the bill."""
        for line in self.lines:
            if line.part["id"] == part_id:
                if line.qty.value() >= line.qty.maximum():
                    widgets.show_error(self, f"Only {line.qty.maximum()} of {line.part['part_number']} in stock.")
                else:
                    line.qty.setValue(line.qty.value() + 1)
                return
        part = self.conn.execute("SELECT * FROM part WHERE id = ?", (part_id,)).fetchone()
        if part["stock_qty"] <= 0:
            widgets.show_error(self, f"{part['part_number']} is out of stock.")
            return
        qty = QSpinBox()
        qty.setRange(1, part["stock_qty"])
        price = MoneyEdit()
        price.setValue(part["sale_price"])
        discount = MoneyEdit()
        line = _BillLine(part, qty, price, discount)
        self.lines.append(line)
        qty.valueChanged.connect(self.update_totals)
        price.textChanged.connect(self.update_totals)
        discount.textChanged.connect(self.update_totals)

        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, text in ((0, part["part_number"]), (1, part["name"]), (2, str(part["stock_qty"])),
                        (6, f"{part['gst_rate_bp'] / 100:g}%")):
            self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.setCellWidget(r, 3, qty)
        self.table.setCellWidget(r, 4, price)
        self.table.setCellWidget(r, 5, discount)
        self.table.setItem(r, 7, QTableWidgetItem())
        remove = QPushButton("X")
        remove.setObjectName("small")
        remove.setToolTip("Remove this line")
        remove.clicked.connect(lambda: self.remove_line(line))
        self.table.setCellWidget(r, 8, remove)
        self.update_totals()

    def remove_line(self, line: _BillLine) -> None:
        r = self.lines.index(line)
        self.lines.pop(r)
        self.table.removeRow(r)
        self.update_totals()

    # ------------------------------------------------------------ payments & totals

    def add_payment_row(self) -> None:
        row = PaymentRow(self.update_totals, self._remove_payment_row)
        self.payment_rows.append(row)
        self.payments_layout.insertWidget(self.payments_layout.count() - 1, row)

    def _drop_payment_row(self, row: PaymentRow) -> None:
        self.payment_rows.remove(row)
        self.payments_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    def _remove_payment_row(self, row: PaymentRow) -> None:
        self._drop_payment_row(row)
        if not self.payment_rows:
            self.add_payment_row()
        self.update_totals()

    def bill_total(self) -> int:
        return round_to_rupee(sum(line.amount() for line in self.lines))[0]

    def update_totals(self) -> None:
        for r, line in enumerate(self.lines):
            self.table.item(r, 7).setText(format_inr(line.amount()))
            self.table.item(r, 7).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        total = self.bill_total()
        # Counter sales are usually paid in full in one go: keep the first payment equal to the bill
        # until the cashier types an amount.
        first = self.payment_rows[0] if self.payment_rows else None
        if first is not None and not first.typed and len(self.payment_rows) == 1:
            first.amount.blockSignals(True)
            first.amount.setValue(total or None)
            first.amount.blockSignals(False)
        paying = 0
        for row in self.payment_rows:
            try:
                paying += row.amount.value() or 0
            except ValueError:
                pass
        self.total_labels["items"].setText(str(sum(line.qty.value() for line in self.lines)))
        self.total_labels["total"].setText(format_inr(total))
        self.total_labels["paying"].setText(format_inr(paying))
        self.total_labels["balance"].setText(format_inr(total - paying))
        self.total_labels["balance"].setProperty("alert", total != paying)
        self.total_labels["balance"].style().polish(self.total_labels["balance"])

    def _customer_changed(self, customer_id: int | None) -> None:
        for edit in (self.walk_in_name, self.walk_in_phone):
            edit.setEnabled(customer_id is None)

    # ------------------------------------------------------------ create

    def create_bill(self) -> int | None:
        try:
            if not self.lines:
                raise BusinessError("Add at least one part to the bill.")
            part_lines = []
            for line in self.lines:
                price = line.price.value()
                if price is None:
                    raise BusinessError(f"Enter the price for {line.part['part_number']}.")
                part_lines.append(PartLine(line.part["id"], line.qty.value(), price, line.discount.value() or 0))
            payments = [p for p in (row.payment() for row in self.payment_rows) if p is not None]
            phone = self.walk_in_phone.text().strip()
            if phone and normalize_mobile(phone) is None:
                raise BusinessError("Walk-in mobile number is not valid.")
            total = self.bill_total()
            if not widgets.confirm(self, f"Create bill for {format_inr(total)}?"):
                return None
            invoice_id = complete_part_sale(
                self.conn, lines=part_lines, payments=payments, customer_id=self.customer.customer_id(),
                walk_in_name=self.walk_in_name.text().strip() or WALK_IN_NAME,
                walk_in_phone=normalize_mobile(phone) if phone else None,
            )
        except (BusinessError, ValueError) as exc:
            widgets.show_error(self, str(exc))
            return None
        invoice_no = self.conn.execute("SELECT invoice_no FROM invoice WHERE id = ?", (invoice_id,)).fetchone()[0]
        widgets.show_info(self, f"Bill {invoice_no} created.")
        self.reset()
        return invoice_id

    def reset(self) -> None:
        self.lines.clear()
        self.table.setRowCount(0)
        for row in list(self.payment_rows):
            self._drop_payment_row(row)
        self.add_payment_row()
        self.walk_in_name.clear()
        self.walk_in_phone.clear()
        self.customer.set_customer(None)
        self.reload_parts()
        self.update_totals()
        self.lookup.setFocus()

    def showEvent(self, event) -> None:
        self.reload_parts()
        super().showEvent(event)
        self.lookup.setFocus()
