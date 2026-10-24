"""Bookings: take an advance, allot a vehicle, then sell it through Sell Vehicle."""

import sqlite3
from collections.abc import Callable

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from jbhonda.core.bookings import (
    add_advance, advance_held, allot_vehicle, cancel_booking, create_booking, find_bookings, refund_advance,
)
from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import vehicle_colors
from jbhonda.core.money import format_inr
from jbhonda.ui import widgets
from jbhonda.ui.customer_widgets import CustomerPicker
from jbhonda.ui.sell_vehicle_page import PAYMENT_MODE_CHOICES, vehicle_label
from jbhonda.ui.widgets import ChoiceCombo, Column, DataTable, FormDialog, MoneyEdit, format_date

STATUS_LABELS = {"OPEN": "Open", "ALLOTTED": "Vehicle allotted", "CONVERTED": "Sold", "CANCELLED": "Cancelled"}


class NewBookingDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__("New booking", parent)
        self.setMinimumWidth(520)
        self.conn = conn
        self.booking_id: int | None = None
        models = conn.execute("SELECT * FROM vehicle_model WHERE is_active = 1 ORDER BY model_name, variant").fetchall()
        self.prices = {m["id"]: m["ex_showroom_price"] for m in models}
        self.customer = CustomerPicker(conn)
        self.model = ChoiceCombo([(f"{m['model_name']} {m['variant']}".strip(), m["id"]) for m in models])
        self.color = QComboBox()
        self.color.setEditable(True)
        self.color.addItems(vehicle_colors(conn))
        self.color.setCurrentText("")
        self.quoted_price = MoneyEdit()
        self.delivery = QDateEdit(QDate.currentDate().addDays(7))
        self.delivery.setDisplayFormat("dd-MM-yyyy")
        self.delivery.setCalendarPopup(True)
        self.delivery.setMinimumDate(QDate.currentDate())
        self.advance = MoneyEdit()
        self.mode = ChoiceCombo(PAYMENT_MODE_CHOICES)
        self.reference = QLineEdit()
        self.model.currentIndexChanged.connect(lambda: self.quoted_price.setValue(self.prices.get(self.model.value())))
        self.form.addRow("Customer *", self.customer)
        self.form.addRow("Model *", self.model)
        self.form.addRow("Colour wanted", self.color)
        self.form.addRow("Quoted price", self.quoted_price)
        self.form.addRow("Expected delivery", self.delivery)
        self.form.addRow("Advance received", self.advance)
        self.form.addRow("Paid by", self.mode)
        self.form.addRow("Reference no.", self.reference)
        self.quoted_price.setValue(self.prices.get(self.model.value()))

    def save(self) -> None:
        if self.customer.customer_id() is None:
            raise BusinessError("Choose the customer, or add a new one.")
        if self.model.value() is None:
            raise BusinessError("Choose the model (add models under Masters).")
        self.booking_id = create_booking(
            self.conn, customer_id=self.customer.customer_id(), vehicle_model_id=self.model.value(),
            preferred_color=self.color.currentText(), quoted_price=self.quoted_price.value() or 0,
            expected_delivery_date=self.delivery.date().toPython(), advance=self.advance.value() or 0,
            advance_mode=self.mode.value(), reference_no=self.reference.text(),
        )


class AllotVehicleDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, booking_id: int, parent: QWidget | None = None):
        super().__init__("Allot vehicle", parent)
        self.conn, self.booking_id = conn, booking_id
        booking = conn.execute("SELECT * FROM booking WHERE id = ?", (booking_id,)).fetchone()
        vehicles = conn.execute(
            "SELECT v.*, m.model_name, m.variant FROM vehicle v JOIN vehicle_model m ON m.id = v.vehicle_model_id"
            " WHERE v.vehicle_model_id = ? AND (v.status = 'IN_STOCK' OR v.id = ?)"
            " ORDER BY v.color = ? COLLATE NOCASE DESC, v.purchase_date, v.id",  # wanted colour first, oldest stock first
            (booking["vehicle_model_id"], booking["vehicle_id"], booking["preferred_color"] or ""),
        ).fetchall()
        self.vehicle = ChoiceCombo([(vehicle_label(v), v["id"]) for v in vehicles])
        self.vehicle.setValue(booking["vehicle_id"])
        if booking["preferred_color"]:
            self.form.addRow("Colour wanted", QLabel(booking["preferred_color"]))
        self.form.addRow("Vehicle *", self.vehicle)
        if not vehicles:
            self.form.addRow(QLabel("No vehicle of this model is in stock."))

    def save(self) -> None:
        if self.vehicle.value() is None:
            raise BusinessError("No vehicle chosen.")
        allot_vehicle(self.conn, self.booking_id, self.vehicle.value())


class AdvanceDialog(FormDialog):
    """Take more advance, or refund it after cancellation."""

    def __init__(self, conn: sqlite3.Connection, booking_id: int, refund: bool, parent: QWidget | None = None):
        super().__init__("Refund advance" if refund else "Add advance", parent)
        self.conn, self.booking_id, self.refund = conn, booking_id, refund
        held = advance_held(conn, booking_id)
        self.amount = MoneyEdit()
        if refund:
            self.amount.setValue(held)
        self.mode = ChoiceCombo(PAYMENT_MODE_CHOICES)
        self.reference = QLineEdit()
        self.form.addRow("Advance held", QLabel(format_inr(held)))
        self.form.addRow("Amount *", self.amount)
        self.form.addRow("Refunded by" if refund else "Paid by", self.mode)
        self.form.addRow("Reference no.", self.reference)

    def save(self) -> None:
        amount = self.amount.value()
        if not amount:
            raise BusinessError("Enter the amount.")
        action = refund_advance if self.refund else add_advance
        action(self.conn, self.booking_id, amount=amount, mode=self.mode.value(), reference_no=self.reference.text())


class CancelBookingDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, booking_id: int, parent: QWidget | None = None):
        super().__init__("Cancel booking", parent)
        self.conn, self.booking_id = conn, booking_id
        self.reason = QLineEdit()
        self.form.addRow("Reason *", self.reason)
        held = advance_held(conn, booking_id)
        if held:
            self.form.addRow(QLabel(f"{format_inr(held)} advance is held. Refund it after cancelling."))

    def save(self) -> None:
        cancel_booking(self.conn, self.booking_id, self.reason.text())


class BookingsPage(QWidget):
    def __init__(self, conn: sqlite3.Connection, on_sell: Callable[[int], None] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.conn, self.on_sell = conn, on_sell
        title = QLabel("Bookings")
        title.setObjectName("pageTitle")

        self.new_button = QPushButton("New booking")
        self.new_button.setObjectName("primary")
        self.allot_button = QPushButton("Allot vehicle")
        self.advance_button = QPushButton("Add advance")
        self.sell_button = QPushButton("Sell")
        self.cancel_button = QPushButton("Cancel booking")
        self.refund_button = QPushButton("Refund advance")
        self.new_button.clicked.connect(self.new_booking)
        self.allot_button.clicked.connect(lambda: self._run(AllotVehicleDialog))
        self.advance_button.clicked.connect(lambda: self._run(lambda c, b, p: AdvanceDialog(c, b, False, p)))
        self.refund_button.clicked.connect(lambda: self._run(lambda c, b, p: AdvanceDialog(c, b, True, p)))
        self.cancel_button.clicked.connect(lambda: self._run(CancelBookingDialog))
        self.sell_button.clicked.connect(self.sell)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search booking no., customer, mobile or chassis")
        self.search.setClearButtonEnabled(True)
        self.status_filter = ChoiceCombo([
            ("Active", "ACTIVE"), ("All", None), ("Sold", "CONVERTED"), ("Cancelled", "CANCELLED"),
        ])
        self.search.textChanged.connect(self.refresh)
        self.status_filter.currentIndexChanged.connect(self.refresh)

        self.table = DataTable([
            Column("Booking no.", lambda r: r["booking_no"]),
            Column("Date", lambda r: format_date(r["booking_date"]), sort_key=lambda r: r["booking_date"]),
            Column("Customer", lambda r: r["customer_name"]),
            Column("Mobile", lambda r: r["customer_phone"]),
            Column("Model", lambda r: f"{r['model_name']} {r['variant']}".strip()),
            Column("Colour", lambda r: r["vehicle_color"] or r["preferred_color"]),
            Column("Vehicle", lambda r: r["chassis_number"]),
            Column("Delivery", lambda r: format_date(r["expected_delivery_date"]),
                   sort_key=lambda r: r["expected_delivery_date"]),
            Column("Advance held", lambda r: format_inr(r["advance_held"]), align_right=True,
                   sort_key=lambda r: r["advance_held"]),
            Column("Status", lambda r: STATUS_LABELS[r["status"]]),
            Column("Invoice", lambda r: r["invoice_no"]),
        ], highlight=lambda r: r["status"] == "CANCELLED" and r["advance_held"] > 0)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(self.sell)
        self.footer = QLabel()
        self.hint = QLabel("Rows in red: cancelled bookings with advance still to refund.")
        self.hint.setObjectName("cardCaption")

        buttons = QHBoxLayout()
        for b in (self.new_button, self.allot_button, self.advance_button, self.sell_button, self.cancel_button,
                  self.refund_button):
            buttons.addWidget(b)
        buttons.addStretch()
        filters = QHBoxLayout()
        filters.addWidget(self.search, 3)
        filters.addWidget(self.status_filter, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(buttons)
        layout.addLayout(filters)
        layout.addWidget(self.table)
        layout.addWidget(self.footer)
        layout.addWidget(self.hint)
        self.refresh()

    def refresh(self) -> None:
        rows = find_bookings(self.conn, status=self.status_filter.value(), text=self.search.text())
        self.table.fill(rows)
        self._update_buttons()
        held = sum(r["advance_held"] for r in rows)
        self.footer.setText(f"{len(rows)} bookings shown  ·  advance held {format_inr(held)}")

    def _selected(self) -> sqlite3.Row | None:
        booking_id = self.table.selected_id()
        if booking_id is None:
            return None
        return next((r for r in find_bookings(self.conn) if r["id"] == booking_id), None)

    def _update_buttons(self) -> None:
        b = self._selected()
        is_open = b is not None and b["status"] in ("OPEN", "ALLOTTED")
        for button in (self.allot_button, self.advance_button, self.sell_button, self.cancel_button):
            button.setEnabled(is_open)
        self.refund_button.setEnabled(b is not None and b["status"] == "CANCELLED" and b["advance_held"] > 0)

    def new_booking(self) -> None:
        if NewBookingDialog(self.conn, self).exec():
            self.refresh()

    def _run(self, dialog_factory) -> None:
        b = self._selected()
        if b is not None and dialog_factory(self.conn, b["id"], self).exec():
            self.refresh()

    def sell(self) -> None:
        b = self._selected()
        if b is not None and b["status"] in ("OPEN", "ALLOTTED") and self.on_sell is not None:
            self.on_sell(b["id"])

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)
