"""Sell a vehicle: customer, optional booking, vehicle and price, accessories, finance, insurance, payments."""

import sqlite3

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox, QDateEdit, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from jbhonda.core.bookings import advance_held, find_bookings
from jbhonda.core.errors import BusinessError
from jbhonda.core.gst import round_to_rupee
from jbhonda.core.money import format_inr
from jbhonda.core.sales import PartLine
from jbhonda.core.vehicle_sale import POLICY_TYPES, InsuranceIn, LoanIn, complete_vehicle_sale
from jbhonda.ui import widgets
from jbhonda.ui.customer_widgets import CustomerPicker
from jbhonda.ui.invoices_page import offer_to_print
from jbhonda.ui.payments import PaymentRow
from jbhonda.ui.widgets import ChoiceCombo, FormDialog, MoneyEdit

POLICY_LABELS = {"COMPREHENSIVE": "Comprehensive", "THIRD_PARTY": "Third party only",
                 "BUNDLED_1OD_5TP": "Bundled (1 yr OD + 5 yr TP)"}


def vehicle_label(v) -> str:
    model = f"{v['model_name']} {v['variant']}".strip()
    return f"{model}  ·  {v['color']}  ·  {v['chassis_number']}"


class AccessoryDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__("Add accessory / part", parent)
        self.conn = conn
        parts = conn.execute(
            "SELECT * FROM part WHERE is_active = 1 AND stock_qty > 0 ORDER BY category <> 'ACCESSORY', name"
        ).fetchall()
        self.parts = {p["id"]: p for p in parts}
        self.part = ChoiceCombo([(f"{p['name']} ({p['part_number']}) - {p['stock_qty']} in stock", p["id"])
                                 for p in parts])
        self.part.setEditable(True)  # type to search
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 1000)
        self.price = MoneyEdit()
        self.part.currentIndexChanged.connect(self._part_changed)
        self.form.addRow("Part *", self.part)
        self.form.addRow("Quantity", self.quantity)
        self.form.addRow("Price each (incl. GST)", self.price)
        self.result_line: tuple[sqlite3.Row, int, int] | None = None
        self._part_changed()

    def _part_changed(self) -> None:
        part = self.parts.get(self.part.value())
        if part is not None:
            self.quantity.setMaximum(part["stock_qty"])
            self.price.setValue(part["sale_price"])

    def save(self) -> None:
        part = self.parts.get(self.part.value())
        if part is None:
            raise BusinessError("Choose a part that is in stock.")
        price = self.price.value()
        if price is None:
            raise BusinessError("Enter the price.")
        if part["mrp"] and price > part["mrp"]:
            raise BusinessError(f"Price cannot be more than the MRP ({format_inr(part['mrp'])}).")
        self.result_line = (part, self.quantity.value(), price)


class SellVehiclePage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self.accessories: list[tuple[sqlite3.Row, int, int]] = []
        self._priced_vehicle: int | None = None  # vehicle the price box was last filled for
        self.payment_rows: list[PaymentRow] = []

        title = QLabel("Sell vehicle")
        title.setObjectName("pageTitle")

        # -- customer & booking
        self.customer = CustomerPicker(conn)
        self.customer_details = QLabel()
        self.customer_details.setWordWrap(True)
        self.booking = ChoiceCombo([("No booking", None)])
        self.booking_info = QLabel()
        customer_box = QGroupBox("1. Customer")
        f = QFormLayout(customer_box)
        f.addRow(self.customer)
        f.addRow(self.customer_details)
        f.addRow("Booking", self.booking)
        f.addRow("", self.booking_info)

        # -- vehicle & price
        self.model_filter = ChoiceCombo([("All models", None)])
        self.vehicle = ChoiceCombo([])
        self.price = MoneyEdit()
        self.discount = MoneyEdit()
        vehicle_box = QGroupBox("2. Vehicle")
        f = QFormLayout(vehicle_box)
        f.addRow("Model", self.model_filter)
        f.addRow("Vehicle *", self.vehicle)
        f.addRow("Price (incl. GST) *", self.price)
        f.addRow("Discount", self.discount)

        # -- accessories
        self.accessory_table = QTableWidget(0, 4)
        self.accessory_table.setHorizontalHeaderLabels(["Item", "Qty", "Price", "Amount"])
        self.accessory_table.horizontalHeader().setStretchLastSection(True)
        self.accessory_table.verticalHeader().setVisible(False)
        self.accessory_table.setMaximumHeight(130)
        add_accessory = QPushButton("Add accessory")
        remove_accessory = QPushButton("Remove selected")
        add_accessory.clicked.connect(self.add_accessory)
        remove_accessory.clicked.connect(self.remove_accessory)
        accessories_box = QGroupBox("3. Accessories (optional)")
        v = QVBoxLayout(accessories_box)
        v.addWidget(self.accessory_table)
        row = QHBoxLayout()
        row.addWidget(add_accessory)
        row.addWidget(remove_accessory)
        row.addStretch()
        v.addLayout(row)

        # -- finance
        self.finance_box = QGroupBox("4. Finance")
        self.finance_box.setCheckable(True)
        self.finance_box.setChecked(False)
        self.financer = ChoiceCombo([])
        self.loan_amount = MoneyEdit()
        self.tenure = QSpinBox()
        self.tenure.setRange(0, 120)
        self.tenure.setSuffix(" months")
        self.tenure.setSpecialValueText("-")
        self.emi = MoneyEdit()
        self.interest = QDoubleSpinBox()
        self.interest.setRange(0, 60)
        self.interest.setSuffix(" %")
        self.interest.setSpecialValueText("-")
        self.loan_account = QLineEdit()
        self.loan_received = QCheckBox("Bank has already paid this amount")
        f = QFormLayout(self.finance_box)
        f.addRow("Financer *", self.financer)
        f.addRow("Loan amount *", self.loan_amount)
        f.addRow("Tenure", self.tenure)
        f.addRow("EMI", self.emi)
        f.addRow("Interest rate", self.interest)
        f.addRow("Loan account no.", self.loan_account)
        f.addRow("", self.loan_received)

        # -- insurance
        self.insurance_box = QGroupBox("5. Insurance")
        self.insurance_box.setCheckable(True)
        self.insurance_box.setChecked(False)
        self.insurer = ChoiceCombo([])
        self.policy_number = QLineEdit()
        self.policy_type = ChoiceCombo([(POLICY_LABELS[t], t) for t in POLICY_TYPES])
        self.policy_type.setValue("BUNDLED_1OD_5TP")
        self.premium = MoneyEdit()
        self.idv = MoneyEdit()
        self.policy_start = QDateEdit(QDate.currentDate())
        self.policy_end = QDateEdit(QDate.currentDate().addYears(1).addDays(-1))
        for d in (self.policy_start, self.policy_end):
            d.setDisplayFormat("dd-MM-yyyy")
            d.setCalendarPopup(True)
        self.policy_start.dateChanged.connect(lambda d: self.policy_end.setDate(d.addYears(1).addDays(-1)))
        self.nominee = QLineEdit()
        self.nominee_relation = QLineEdit()
        f = QFormLayout(self.insurance_box)
        f.addRow("Company *", self.insurer)
        f.addRow("Policy number *", self.policy_number)
        f.addRow("Policy type", self.policy_type)
        f.addRow("Premium", self.premium)
        f.addRow("IDV", self.idv)
        f.addRow("Start date", self.policy_start)
        f.addRow("End date", self.policy_end)
        f.addRow("Nominee", self.nominee)
        f.addRow("Nominee relation", self.nominee_relation)

        # -- payments
        payments_box = QGroupBox("6. Payment received now")
        self.payments_layout = QVBoxLayout(payments_box)
        add_payment = QPushButton("Add another payment")
        add_payment.clicked.connect(self.add_payment_row)
        self.payments_layout.addWidget(add_payment)

        # -- summary
        summary_box = QGroupBox("Summary")
        self.summary = QGridLayout(summary_box)
        self.summary_labels: dict[str, QLabel] = {}
        for i, (key, text) in enumerate((
            ("vehicle", "Vehicle"), ("discount", "Discount"), ("accessories", "Accessories"),
            ("total", "Bill total"), ("advance", "Booking advance"), ("loan", "Loan"),
            ("paying", "Paying now"), ("balance", "Balance due"),
        )):
            label = QLabel(text)
            value = QLabel()
            value.setObjectName("summaryValue")
            self.summary.addWidget(label, i, 0)
            self.summary.addWidget(value, i, 1)
            self.summary_labels[key] = value
        self.summary_labels["total"].setObjectName("summaryTotal")
        self.create_button = QPushButton("Create invoice")
        self.create_button.setObjectName("primary")
        self.create_button.setMinimumHeight(40)
        clear_button = QPushButton("Clear")
        self.create_button.clicked.connect(self.create_invoice)
        clear_button.clicked.connect(self.reset)

        # -- layout
        left = QVBoxLayout()
        for w in (customer_box, vehicle_box, accessories_box):
            left.addWidget(w)
        left.addStretch()
        right = QVBoxLayout()
        for w in (self.finance_box, self.insurance_box, payments_box, summary_box):
            right.addWidget(w)
        buttons = QHBoxLayout()
        buttons.addWidget(clear_button)
        buttons.addWidget(self.create_button, 1)
        right.addLayout(buttons)
        right.addStretch()
        columns = QHBoxLayout()
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(title)
        content_layout.addLayout(columns)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # -- wiring
        self.customer.changed.connect(self._customer_changed)
        self.booking.currentIndexChanged.connect(self._booking_changed)
        self.model_filter.currentIndexChanged.connect(self._reload_vehicles)
        self.vehicle.currentIndexChanged.connect(self._vehicle_changed)
        for edit in (self.price, self.discount, self.loan_amount):
            edit.textChanged.connect(self.update_summary)
        self.finance_box.toggled.connect(self.update_summary)
        self.reload_lists()
        self.add_payment_row()

    # ------------------------------------------------------------ lists

    def reload_lists(self) -> None:
        """Refresh pick lists that other screens can change, keeping current choices."""
        models = self.conn.execute(
            "SELECT * FROM vehicle_model WHERE is_active = 1 ORDER BY model_name, variant").fetchall()
        self._refill(self.model_filter, [("All models", None),
                                         *((f"{m['model_name']} {m['variant']}".strip(), m["id"]) for m in models)])
        financers = self.conn.execute("SELECT * FROM financer WHERE is_active = 1 ORDER BY name, branch").fetchall()
        self._refill(self.financer, [(f"{x['name']} {x['branch']}".strip(), x["id"]) for x in financers])
        insurers = self.conn.execute("SELECT * FROM insurer WHERE is_active = 1 ORDER BY name").fetchall()
        self._refill(self.insurer, [(x["name"], x["id"]) for x in insurers])
        self._reload_vehicles()

    @staticmethod
    def _refill(combo: ChoiceCombo, items) -> None:
        current = combo.value()
        combo.blockSignals(True)
        combo.clear()
        for label, value in items:
            combo.addItem(label, value)
        combo.setValue(current)
        combo.blockSignals(False)

    def _selected_booking(self) -> sqlite3.Row | None:
        booking_id = self.booking.value()
        if booking_id is None:
            return None
        return self.conn.execute("SELECT * FROM booking WHERE id = ?", (booking_id,)).fetchone()

    def _reload_vehicles(self) -> None:
        booking = self._selected_booking()
        where, args = ["v.status = 'IN_STOCK'"], []
        if self.model_filter.value() is not None:
            where.append("v.vehicle_model_id = ?")
            args.append(self.model_filter.value())
        if booking is not None and booking["vehicle_id"] is not None:
            where = [f"(({' AND '.join(where)}) OR v.id = ?)"]
            args.append(booking["vehicle_id"])
        rows = self.conn.execute(
            "SELECT v.*, m.model_name, m.variant FROM vehicle v JOIN vehicle_model m ON m.id = v.vehicle_model_id"
            f" WHERE {' AND '.join(where)} ORDER BY m.model_name, v.color, v.purchase_date, v.id",
            args,
        ).fetchall()
        self._refill(self.vehicle, [("- choose a vehicle -", None), *((vehicle_label(v), v["id"]) for v in rows)])
        self._vehicle_changed()

    # ------------------------------------------------------------ reactions

    def _customer_changed(self, customer_id: int | None) -> None:
        details = ""
        if customer_id is not None:
            c = self.conn.execute("SELECT * FROM customer WHERE id = ?", (customer_id,)).fetchone()
            parts = [f"{c['relation_type']} {c['relation_name']}" if c["relation_type"] and c["relation_name"] else "",
                     ", ".join(x for x in (c["address_line1"], c["city"], c["district"], c["pincode"]) if x)]
            details = "  ·  ".join(p for p in parts if p) or "No address on file - click Edit to add it."
        self.customer_details.setText(details)
        bookings = find_bookings(self.conn, status="ACTIVE", customer_id=customer_id) if customer_id else []
        self._refill(self.booking, [("No booking", None), *(
            (f"{b['booking_no']} - {b['model_name']} {b['variant']}".strip(), b["id"]) for b in bookings)])
        if len(bookings) == 1:
            self.booking.setCurrentIndex(1)
        self._booking_changed()

    def _booking_changed(self) -> None:
        booking = self._selected_booking()
        if booking is None:
            self.booking_info.setText("")
        else:
            self.model_filter.blockSignals(True)
            self.model_filter.setValue(booking["vehicle_model_id"])
            self.model_filter.blockSignals(False)
            self.booking_info.setText(
                f"Advance held: {format_inr(advance_held(self.conn, booking['id']))}"
                + (f"  ·  colour wanted: {booking['preferred_color']}" if booking["preferred_color"] else "")
            )
        self._reload_vehicles()
        if booking is not None and booking["vehicle_id"] is not None:
            self.vehicle.setValue(booking["vehicle_id"])
        self.update_summary()

    def _vehicle_changed(self) -> None:
        """Fill in the default price, but only when the vehicle actually changes (keeps a typed price)."""
        vehicle_id = self.vehicle.value()
        if vehicle_id == self._priced_vehicle:
            self.update_summary()
            return
        self._priced_vehicle = vehicle_id
        if vehicle_id is None:
            self.price.clear()
        else:
            booking = self._selected_booking()
            price = self.conn.execute(
                "SELECT m.ex_showroom_price FROM vehicle v JOIN vehicle_model m ON m.id = v.vehicle_model_id"
                " WHERE v.id = ?", (vehicle_id,)).fetchone()[0]
            if booking is not None and booking["quoted_price"]:
                price = booking["quoted_price"]
            self.price.setValue(price)
        self.update_summary()

    # ------------------------------------------------------------ accessories & payments

    def add_accessory(self) -> None:
        dialog = AccessoryDialog(self.conn, self)
        if dialog.exec() and dialog.result_line:
            self.accessories.append(dialog.result_line)
            self._show_accessories()

    def remove_accessory(self) -> None:
        rows = sorted({i.row() for i in self.accessory_table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.accessories[r]
        self._show_accessories()

    def _show_accessories(self) -> None:
        self.accessory_table.setRowCount(len(self.accessories))
        for r, (part, qty, price) in enumerate(self.accessories):
            for c, text in enumerate((part["name"], str(qty), format_inr(price), format_inr(qty * price))):
                self.accessory_table.setItem(r, c, QTableWidgetItem(text))
        self.accessory_table.resizeColumnsToContents()
        self.update_summary()

    def add_payment_row(self) -> None:
        row = PaymentRow(self.update_summary, self._remove_payment_row)
        self.payment_rows.append(row)
        self.payments_layout.insertWidget(self.payments_layout.count() - 1, row)

    def _drop_payment_row(self, row: PaymentRow) -> None:
        self.payment_rows.remove(row)
        self.payments_layout.removeWidget(row)
        row.setParent(None)  # off screen now, not whenever Qt gets round to deleting it
        row.deleteLater()

    def _remove_payment_row(self, row: PaymentRow) -> None:
        self._drop_payment_row(row)
        if not self.payment_rows:
            self.add_payment_row()
        self.update_summary()

    # ------------------------------------------------------------ totals

    def _money(self, edit: MoneyEdit) -> int:
        try:
            return edit.value() or 0
        except ValueError:
            return 0

    def totals(self) -> dict[str, int]:
        price, discount = self._money(self.price), self._money(self.discount)
        accessories = sum(qty * p for _, qty, p in self.accessories)
        total, _ = round_to_rupee(max(price - discount, 0) + accessories) if self.vehicle.value() else (0, 0)
        booking = self._selected_booking()
        advance = advance_held(self.conn, booking["id"]) if booking is not None else 0
        loan = self._money(self.loan_amount) if self.finance_box.isChecked() else 0
        paying = 0
        for row in self.payment_rows:
            try:
                paying += row.amount.value() or 0
            except ValueError:
                pass
        return {"vehicle": price, "discount": discount, "accessories": accessories, "total": total,
                "advance": advance, "loan": loan, "paying": paying,
                "balance": total - advance - loan - paying}

    def update_summary(self) -> None:
        t = self.totals()
        for key, value in t.items():
            text = format_inr(value)
            if key in ("discount", "advance", "loan", "paying") and value:
                text = "- " + text
            self.summary_labels[key].setText(text)
        self.summary_labels["balance"].setProperty("alert", t["balance"] != 0)
        self.summary_labels["balance"].style().polish(self.summary_labels["balance"])

    # ------------------------------------------------------------ create

    def _loan(self) -> LoanIn | None:
        if not self.finance_box.isChecked():
            return None
        if self.financer.value() is None:
            raise BusinessError("Choose the financer (add financers under Masters).")
        amount = self.loan_amount.value()
        if not amount:
            raise BusinessError("Enter the loan amount.")
        return LoanIn(
            financer_id=self.financer.value(), loan_amount=amount, tenure_months=self.tenure.value() or None,
            emi_amount=self.emi.value(), interest_rate_bp=round(self.interest.value() * 100) or None,
            loan_account_no=self.loan_account.text(), disbursed=self.loan_received.isChecked(),
        )

    def _insurance(self) -> InsuranceIn | None:
        if not self.insurance_box.isChecked():
            return None
        if self.insurer.value() is None:
            raise BusinessError("Choose the insurance company (add them under Masters).")
        return InsuranceIn(
            insurer_id=self.insurer.value(), policy_number=self.policy_number.text(),
            policy_type=self.policy_type.value(), premium=self.premium.value() or 0, idv=self.idv.value(),
            start_date=self.policy_start.date().toPython(), end_date=self.policy_end.date().toPython(),
            nominee_name=self.nominee.text(), nominee_relation=self.nominee_relation.text(),
        )

    def create_invoice(self) -> int | None:
        try:
            customer_id = self.customer.customer_id()
            if customer_id is None:
                raise BusinessError("Choose the customer, or add a new one.")
            if self.vehicle.value() is None:
                raise BusinessError("Choose the vehicle.")
            price = self.price.value()
            if price is None:
                raise BusinessError("Enter the price.")
            payments = [p for p in (row.payment() for row in self.payment_rows) if p is not None]
            loan, insurance = self._loan(), self._insurance()
            t = self.totals()
            if not widgets.confirm(
                self,
                f"Create invoice for {format_inr(t['total'])}?\n\n"
                f"Balance due after this: {format_inr(t['balance'])}",
            ):
                return None
            invoice_id = complete_vehicle_sale(
                self.conn, customer_id=customer_id, vehicle_id=self.vehicle.value(), sale_price=price,
                discount=self.discount.value() or 0,
                accessories=[PartLine(part["id"], qty, p) for part, qty, p in self.accessories],
                payments=payments, loan=loan, insurance=insurance, booking_id=self.booking.value(),
            )
        except (BusinessError, ValueError) as exc:
            widgets.show_error(self, str(exc))
            return None
        self.reset()
        offer_to_print(self, self.conn, invoice_id)
        return invoice_id

    def reset(self) -> None:
        self.accessories.clear()
        self._show_accessories()
        for row in list(self.payment_rows):
            self._drop_payment_row(row)
        self.add_payment_row()
        for edit in (self.discount, self.loan_amount, self.emi, self.loan_account, self.policy_number,
                     self.premium, self.idv, self.nominee, self.nominee_relation):
            edit.clear()
        self.tenure.setValue(0)
        self.interest.setValue(0)
        self.loan_received.setChecked(False)
        self.finance_box.setChecked(False)
        self.insurance_box.setChecked(False)
        self.policy_start.setDate(QDate.currentDate())
        self._priced_vehicle = None
        self.model_filter.setValue(None)
        self.customer.set_customer(None)
        self.reload_lists()

    def load_booking(self, booking_id: int) -> None:
        """Open this screen pre-filled from a booking (used by the Bookings screen)."""
        booking = self.conn.execute("SELECT * FROM booking WHERE id = ?", (booking_id,)).fetchone()
        self.reset()
        self.customer.set_customer(booking["customer_id"])
        self.booking.setValue(booking_id)
        self._booking_changed()

    def showEvent(self, event) -> None:
        self.reload_lists()
        super().showEvent(event)
