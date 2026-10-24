"""Workshop: open a job card when a vehicle comes in, add parts and labour, bill on delivery."""

import sqlite3
from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox, QDateEdit, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from jbhonda.core.errors import BusinessError
from jbhonda.core.money import format_inr
from jbhonda.core.service import (
    SERVICE_TYPES, add_job_labour, add_job_part, bill_job, cancel_job, create_job, customer_vehicles, find_jobs,
    job_lines, job_totals, remove_job_labour, remove_job_part, update_job,
)
from jbhonda.ui import widgets
from jbhonda.ui.customer_widgets import CustomerPicker
from jbhonda.ui.invoices_page import offer_to_print, print_invoice
from jbhonda.ui.payments import PaymentRow
from jbhonda.ui.sell_vehicle_page import AccessoryDialog
from jbhonda.ui.widgets import ChoiceCombo, Column, DataTable, FormDialog, MoneyEdit, format_date

TYPE_LABELS = {"PAID": "Paid service", "FREE": "Free service", "WARRANTY": "Warranty", "ACCIDENT": "Accident repair"}
STATUS_LABELS = {"OPEN": "In workshop", "BILLED": "Delivered", "CANCELLED": "Cancelled"}


def _type_text(job) -> str:
    if job["service_type"] == "FREE":
        return f"Free service {job['free_service_no']}"
    return TYPE_LABELS[job["service_type"]]


class _JobHeader(QWidget):
    """Vehicle and complaint fields shared by the new-job and job-card dialogs."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.vehicle_description = QLineEdit()
        self.vehicle_description.setPlaceholderText("e.g. Activa 6G")
        self.registration = QLineEdit()
        self.registration.setPlaceholderText("e.g. UP80 AB 1234")
        self.chassis = QLineEdit()
        self.odometer = QSpinBox()
        self.odometer.setRange(0, 999999)
        self.odometer.setSuffix(" km")
        self.service_type = ChoiceCombo([(TYPE_LABELS[t], t) for t in SERVICE_TYPES])
        self.free_no = QSpinBox()
        self.free_no.setRange(1, 6)
        self.free_no.setPrefix("Free service ")
        self.complaints = QPlainTextEdit()
        self.complaints.setFixedHeight(60)
        self.mechanic = QLineEdit()
        self.service_type.currentIndexChanged.connect(
            lambda: self.free_no.setEnabled(self.service_type.value() == "FREE"))
        self.free_no.setEnabled(False)
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.addRow("Vehicle model *", self.vehicle_description)
        self.form.addRow("Registration no.", self.registration)
        self.form.addRow("Chassis no.", self.chassis)
        self.form.addRow("Odometer", self.odometer)
        type_row = QHBoxLayout()
        type_row.addWidget(self.service_type, 2)
        type_row.addWidget(self.free_no, 1)
        self.form.addRow("Service type", type_row)
        self.form.addRow("Complaints", self.complaints)
        self.form.addRow("Mechanic", self.mechanic)

    def values(self) -> dict:
        return {
            "vehicle_description": self.vehicle_description.text(),
            "registration_number": self.registration.text(),
            "chassis_number": self.chassis.text(),
            "odometer_km": self.odometer.value() or None,
            "service_type": self.service_type.value(),
            "free_service_no": self.free_no.value() if self.service_type.value() == "FREE" else None,
            "complaints": self.complaints.toPlainText(),
            "mechanic_name": self.mechanic.text(),
        }

    def load(self, job) -> None:
        self.vehicle_description.setText(job["vehicle_description"])
        self.registration.setText(job["registration_number"] or "")
        self.chassis.setText(job["chassis_number"] or "")
        self.odometer.setValue(job["odometer_km"] or 0)
        self.service_type.setValue(job["service_type"])
        self.free_no.setValue(job["free_service_no"] or 1)
        self.complaints.setPlainText(job["complaints"] or "")
        self.mechanic.setText(job["mechanic_name"] or "")


class NewJobDialog(FormDialog):
    """Vehicle has arrived: who, which vehicle, what's wrong."""

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__("New job card", parent)
        self.setMinimumWidth(560)
        self.conn = conn
        self.job_id: int | None = None
        self.customer = CustomerPicker(conn)
        self.known_vehicles = ChoiceCombo([("Other vehicle (type below)", None)])
        self.header = _JobHeader()
        self.form.addRow("Customer *", self.customer)
        self.form.addRow("Their vehicles", self.known_vehicles)
        self.form.addRow(self.header)
        self.customer.changed.connect(self._customer_changed)
        self.known_vehicles.currentIndexChanged.connect(self._vehicle_picked)
        self._vehicles: list[dict] = []

    def _customer_changed(self, customer_id: int | None) -> None:
        self._vehicles = customer_vehicles(self.conn, customer_id) if customer_id else []
        self.known_vehicles.blockSignals(True)
        self.known_vehicles.clear()
        self.known_vehicles.addItem("Other vehicle (type below)", None)
        for i, v in enumerate(self._vehicles):
            label = "  ·  ".join(x for x in (v["vehicle_description"], v["registration_number"], v["chassis_number"]) if x)
            self.known_vehicles.addItem(label, i)
        self.known_vehicles.setCurrentIndex(1 if self._vehicles else 0)
        self.known_vehicles.blockSignals(False)
        self._vehicle_picked()

    def _vehicle_picked(self) -> None:
        index = self.known_vehicles.value()
        v = self._vehicles[index] if index is not None else None
        self.header.vehicle_description.setText(v["vehicle_description"] if v else "")
        self.header.registration.setText((v or {}).get("registration_number") or "")
        self.header.chassis.setText((v or {}).get("chassis_number") or "")

    def save(self) -> None:
        if self.customer.customer_id() is None:
            raise BusinessError("Choose the customer, or add a new one.")
        index = self.known_vehicles.value()
        vehicle_id = self._vehicles[index]["vehicle_id"] if index is not None else None
        self.job_id = create_job(self.conn, customer_id=self.customer.customer_id(), vehicle_id=vehicle_id,
                                 **self.header.values())


class LabourDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, free_service: bool, parent: QWidget | None = None):
        super().__init__("Add labour", parent)
        items = conn.execute("SELECT * FROM labour_item WHERE is_active = 1 ORDER BY name").fetchall()
        self.items = {i["id"]: i for i in items}
        self.free_service = free_service
        self.item = ChoiceCombo([("Other (type below)", None), *((i["name"], i["id"]) for i in items)])
        self.description = QLineEdit()
        self.amount = MoneyEdit()
        self.item.currentIndexChanged.connect(self._picked)
        self.form.addRow("Labour", self.item)
        self.form.addRow("Description", self.description)
        self.form.addRow("Charge (incl. GST)", self.amount)
        if free_service:
            note = QLabel("Free service: labour is normally ₹0 for the customer.")
            note.setObjectName("hint")
            self.form.addRow(note)
        self.result_values: dict | None = None
        if items:
            self.item.setCurrentIndex(1)
        self._picked()

    def _picked(self) -> None:
        item = self.items.get(self.item.value())
        self.description.setText(item["name"] if item else "")
        charge = 0 if self.free_service else (item["default_charge"] if item else None)
        self.amount.setValue(charge)

    def save(self) -> None:
        amount = self.amount.value()
        if amount is None:
            raise BusinessError("Enter the charge (0 if free).")
        if not self.description.text().strip():
            raise BusinessError("Describe the labour.")
        self.result_values = {"labour_item_id": self.item.value(), "description": self.description.text(),
                              "amount": amount}


class JobCardDialog(QDialog):
    """The job card while the vehicle is in the workshop: details, parts issued, labour."""

    def __init__(self, conn: sqlite3.Connection, job_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn, self.job_id = conn, job_id
        self.resize(900, 720)
        self.billed_invoice_id: int | None = None
        job = self._job()
        self.setWindowTitle(f"Job card {job['job_no']}")
        customer = conn.execute("SELECT * FROM customer WHERE id = ?", (job["customer_id"],)).fetchone()
        editable = job["status"] == "OPEN"

        info = QLabel(f"<b>{job['job_no']}</b>  ·  received {format_date(job['received_at'])}  ·  "
                      f"{customer['name']} ({customer['phone']})  ·  {STATUS_LABELS[job['status']]}")
        self.header = _JobHeader()
        self.header.load(job)
        self.work_done = QPlainTextEdit(job["work_done"] or "")
        self.work_done.setFixedHeight(50)
        self.header.form.addRow("Work done", self.work_done)
        save_details = QPushButton("Save details")
        save_details.clicked.connect(self.save_details)
        details = QGroupBox("Vehicle && complaint")
        v = QVBoxLayout(details)
        v.addWidget(self.header)
        v.addWidget(save_details)

        self.parts_table = QTableWidget(0, 5)
        self.parts_table.setHorizontalHeaderLabels(["Part", "Qty", "Price", "Discount", "Amount"])
        self.labour_table = QTableWidget(0, 2)
        self.labour_table.setHorizontalHeaderLabels(["Labour", "Charge"])
        self.labour_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for t in (self.parts_table, self.labour_table):
            t.verticalHeader().setVisible(False)
            t.horizontalHeader().setStretchLastSection(True)
            t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        add_part, remove_part = QPushButton("Issue part"), QPushButton("Return selected part")
        add_labour, remove_labour = QPushButton("Add labour"), QPushButton("Remove selected labour")
        add_part.clicked.connect(self.add_part)
        remove_part.clicked.connect(self.remove_part)
        add_labour.clicked.connect(self.add_labour)
        remove_labour.clicked.connect(self.remove_labour)
        parts_box, labour_box = QGroupBox("Parts used (taken from stock)"), QGroupBox("Labour")
        for box, table, buttons in ((parts_box, self.parts_table, (add_part, remove_part)),
                                    (labour_box, self.labour_table, (add_labour, remove_labour))):
            lay = QVBoxLayout(box)
            lay.addWidget(table)
            row = QHBoxLayout()
            for b in buttons:
                b.setEnabled(editable)
                row.addWidget(b)
            row.addStretch()
            lay.addLayout(row)

        self.totals = QLabel()
        self.totals.setObjectName("summaryTotal")
        self.bill_button = QPushButton("Bill && deliver...")
        self.bill_button.setObjectName("primary")
        self.bill_button.setEnabled(editable)
        self.bill_button.clicked.connect(self.bill)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.totals, 1)
        bottom.addWidget(close)
        bottom.addWidget(self.bill_button)

        for w in (self.header, self.work_done, save_details):
            w.setEnabled(editable)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(details)
        lines = QHBoxLayout()
        lines.addWidget(parts_box, 3)
        lines.addWidget(labour_box, 2)
        layout.addLayout(lines, 1)
        layout.addLayout(bottom)
        self.refresh()

    def _job(self):
        return self.conn.execute("SELECT * FROM service_job WHERE id = ?", (self.job_id,)).fetchone()

    def refresh(self) -> None:
        parts, labour = job_lines(self.conn, self.job_id)
        self._parts, self._labour = parts, labour
        self.parts_table.setRowCount(len(parts))
        for r, p in enumerate(parts):
            values = (f"{p['name']} ({p['part_number']})", str(p["quantity"]), format_inr(p["unit_price"]),
                      format_inr(p["discount"]) if p["discount"] else "",
                      format_inr(p["quantity"] * p["unit_price"] - p["discount"]))
            for c, text in enumerate(values):
                self.parts_table.setItem(r, c, QTableWidgetItem(text))
        self.labour_table.setRowCount(len(labour))
        for r, x in enumerate(labour):
            self.labour_table.setItem(r, 0, QTableWidgetItem(x["description"]))
            self.labour_table.setItem(r, 1, QTableWidgetItem(format_inr(x["amount"])))
        self.parts_table.resizeColumnsToContents()
        t = job_totals(self.conn, self.job_id)
        self.totals.setText(f"Parts {format_inr(t.parts)}  +  Labour {format_inr(t.labour)}  =  {format_inr(t.total)}")

    def _run(self, action) -> bool:
        try:
            action()
        except BusinessError as exc:
            widgets.show_error(self, str(exc))
            return False
        self.refresh()
        return True

    def save_details(self) -> bool:
        return self._run(lambda: update_job(self.conn, self.job_id, **self.header.values(),
                                            work_done=self.work_done.toPlainText()))

    def add_part(self) -> None:
        dialog = AccessoryDialog(self.conn, self)
        dialog.setWindowTitle("Issue part to job")
        if dialog.exec() and dialog.result_line:
            part, qty, price = dialog.result_line
            self._run(lambda: add_job_part(self.conn, self.job_id, part_id=part["id"], quantity=qty, unit_price=price))

    def remove_part(self) -> None:
        row = self.parts_table.currentRow()
        if 0 <= row < len(self._parts):
            self._run(lambda: remove_job_part(self.conn, self._parts[row]["id"]))

    def add_labour(self) -> None:
        dialog = LabourDialog(self.conn, free_service=self._job()["service_type"] == "FREE", parent=self)
        if dialog.exec() and dialog.result_values:
            self._run(lambda: add_job_labour(self.conn, self.job_id, **dialog.result_values))

    def remove_labour(self) -> None:
        row = self.labour_table.currentRow()
        if 0 <= row < len(self._labour):
            self._run(lambda: remove_job_labour(self.conn, self._labour[row]["id"]))

    def bill(self) -> None:
        if not self.save_details():
            return
        dialog = BillJobDialog(self.conn, self.job_id, self)
        if dialog.exec():
            self.billed_invoice_id = dialog.invoice_id
            self.accept()


class BillJobDialog(FormDialog):
    """Delivery: confirm the total, note the next service, take payment."""

    def __init__(self, conn: sqlite3.Connection, job_id: int, parent: QWidget | None = None):
        super().__init__("Bill & deliver", parent)
        self.setMinimumWidth(560)
        self.conn, self.job_id = conn, job_id
        self.invoice_id: int | None = None
        job = conn.execute("SELECT * FROM service_job WHERE id = ?", (job_id,)).fetchone()
        self.total = job_totals(conn, job_id).total
        self.next_date = QDateEdit(QDate.currentDate().addDays(90))
        self.next_date.setDisplayFormat("dd-MM-yyyy")
        self.next_date.setCalendarPopup(True)
        self.next_km = QSpinBox()
        self.next_km.setRange(0, 999999)
        self.next_km.setSuffix(" km")
        self.next_km.setSpecialValueText("-")
        self.next_km.setValue((job["odometer_km"] or 0) + 3000 if job["odometer_km"] else 0)
        self.no_next = QCheckBox("No next-service reminder")
        self.payments: list[PaymentRow] = []
        self.payments_box = QVBoxLayout()
        add_payment = QPushButton("Add another payment")
        add_payment.clicked.connect(self.add_payment_row)
        self.payments_box.addWidget(add_payment)
        self.balance = QLabel()
        total = QLabel(format_inr(self.total))
        total.setObjectName("summaryTotal")
        self.form.addRow("Bill total", total)
        self.form.addRow("Next service date", self.next_date)
        self.form.addRow("Next service at", self.next_km)
        self.form.addRow("", self.no_next)
        self.form.addRow("Payment", self.payments_box)
        self.form.addRow("Balance due", self.balance)
        self.buttons.button(self.buttons.StandardButton.Save).setText("Create bill")
        self.add_payment_row()
        self.payments[0].amount.setValue(self.total or None)
        self.update_balance()

    def add_payment_row(self) -> None:
        row = PaymentRow(self.update_balance, self._remove_row)
        self.payments.append(row)
        self.payments_box.insertWidget(self.payments_box.count() - 1, row)

    def _remove_row(self, row: PaymentRow) -> None:
        self.payments.remove(row)
        self.payments_box.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self.update_balance()

    def update_balance(self) -> None:
        paying = 0
        for row in self.payments:
            try:
                paying += row.amount.value() or 0
            except ValueError:
                pass
        self.balance.setText(format_inr(self.total - paying))

    def save(self) -> None:
        payments = [p for p in (row.payment() for row in self.payments) if p is not None]
        remind = not self.no_next.isChecked()
        self.invoice_id = bill_job(
            self.conn, self.job_id, payments=payments,
            next_service_date=self.next_date.date().toPython() if remind else None,
            next_service_km=(self.next_km.value() or None) if remind else None,
        )


class CancelJobDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, job_id: int, parent: QWidget | None = None):
        super().__init__("Cancel job card", parent)
        self.conn, self.job_id = conn, job_id
        self.reason = QLineEdit()
        self.form.addRow("Reason *", self.reason)
        note = QLabel("Parts issued to this job go back into stock.")
        note.setObjectName("hint")
        self.form.addRow(note)
        self.buttons.button(self.buttons.StandardButton.Save).setText("Cancel job card")
        self.buttons.button(self.buttons.StandardButton.Cancel).setText("Keep it")

    def save(self) -> None:
        cancel_job(self.conn, self.job_id, self.reason.text())


class ServicePage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        title = QLabel("Servicing")
        title.setObjectName("pageTitle")
        self.new_button = QPushButton("New job card")
        self.new_button.setObjectName("primary")
        self.open_button = QPushButton("Open job card")
        self.bill_button = QPushButton("Bill && deliver")
        self.print_button = QPushButton("Print bill")
        self.cancel_button = QPushButton("Cancel job card")
        self.new_button.clicked.connect(self.new_job)
        self.open_button.clicked.connect(self.open_job)
        self.bill_button.clicked.connect(self.bill_selected)
        self.print_button.clicked.connect(self.print_selected)
        self.cancel_button.clicked.connect(self.cancel_selected)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search job no., registration no., customer, mobile, model or chassis")
        self.search.setClearButtonEnabled(True)
        self.status_filter = ChoiceCombo([("In workshop", "OPEN"), ("Delivered", "BILLED"), ("Cancelled", "CANCELLED"),
                                          ("All", None)])
        self.search.textChanged.connect(self.refresh)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.table = DataTable([
            Column("Job no.", lambda r: r["job_no"]),
            Column("Received", lambda r: format_date(r["received_at"]), sort_key=lambda r: r["received_at"]),
            Column("Customer", lambda r: r["customer_name"]),
            Column("Mobile", lambda r: r["customer_phone"]),
            Column("Vehicle", lambda r: r["vehicle_description"]),
            Column("Reg. no.", lambda r: r["registration_number"]),
            Column("Km", lambda r: f"{r['odometer_km']:,}" if r["odometer_km"] else "", align_right=True,
                   sort_key=lambda r: r["odometer_km"] or 0),
            Column("Service", _type_text),
            Column("Mechanic", lambda r: r["mechanic_name"]),
            Column("Amount", lambda r: format_inr(r["grand_total"] if r["grand_total"] is not None else r["running_total"]),
                   align_right=True, sort_key=lambda r: r["running_total"]),
            Column("Status", lambda r: STATUS_LABELS[r["status"]]),
            Column("Bill", lambda r: r["invoice_no"]),
        ])
        self.table.doubleClicked.connect(self.open_job)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.footer = QLabel()

        buttons = QHBoxLayout()
        for b in (self.new_button, self.open_button, self.bill_button, self.print_button, self.cancel_button):
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
        self.refresh()

    def refresh(self) -> None:
        rows = find_jobs(self.conn, status=self.status_filter.value(), text=self.search.text())
        self.rows = {r["id"]: r for r in rows}
        self.table.fill(rows)
        self._update_buttons()
        in_workshop = self.conn.execute("SELECT count(*) FROM service_job WHERE status = 'OPEN'").fetchone()[0]
        month_start = date.today().replace(day=1).isoformat()
        free = self.conn.execute(
            "SELECT count(*) FROM service_job j JOIN invoice i ON i.id = j.invoice_id"
            " WHERE j.service_type = 'FREE' AND i.invoice_date >= ?", (month_start,)).fetchone()[0]
        self.footer.setText(f"{in_workshop} vehicles in the workshop  ·  {free} free services delivered this month")

    def selected(self):
        job_id = self.table.selected_id()
        return None if job_id is None else self.rows.get(job_id)

    def _update_buttons(self) -> None:
        job = self.selected()
        is_open = job is not None and job["status"] == "OPEN"
        self.open_button.setEnabled(job is not None)
        self.bill_button.setEnabled(is_open)
        self.cancel_button.setEnabled(is_open)
        self.print_button.setEnabled(job is not None and job["status"] == "BILLED")

    def _show_job(self, job_id: int) -> None:
        dialog = JobCardDialog(self.conn, job_id, self)
        dialog.exec()
        self.refresh()
        if dialog.billed_invoice_id is not None:
            offer_to_print(self, self.conn, dialog.billed_invoice_id)

    def new_job(self) -> None:
        dialog = NewJobDialog(self.conn, self)
        if dialog.exec() and dialog.job_id is not None:
            self.refresh()
            self._show_job(dialog.job_id)

    def open_job(self) -> None:
        job = self.selected()
        if job is not None:
            self._show_job(job["id"])

    def bill_selected(self) -> None:
        job = self.selected()
        if job is None:
            return
        dialog = BillJobDialog(self.conn, job["id"], self)
        if dialog.exec():
            self.refresh()
            offer_to_print(self, self.conn, dialog.invoice_id)

    def print_selected(self) -> None:
        job = self.selected()
        if job is not None and job["invoice_id"]:
            print_invoice(self, self.conn, job["invoice_id"])

    def cancel_selected(self) -> None:
        job = self.selected()
        if job is not None and CancelJobDialog(self.conn, job["id"], self).exec():
            self.refresh()

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)
