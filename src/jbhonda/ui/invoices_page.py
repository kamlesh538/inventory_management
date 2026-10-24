"""All invoices: find, print (PDF), collect balances, record loan money, refund, cancel."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDateEdit, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from jbhonda.core.errors import BusinessError
from jbhonda.core.invoices import find_invoices
from jbhonda.core.money import format_inr
from jbhonda.core.numbering import financial_year
from jbhonda.core.sales import balance_due, cancel_invoice, record_payment
from jbhonda.core.vehicle_sale import record_loan_disbursement
from jbhonda.reports.invoice_pdf import pdf_path, write_invoice_pdf
from jbhonda.ui import widgets
from jbhonda.ui.payments import PAYMENT_MODE_CHOICES
from jbhonda.ui.widgets import ChoiceCombo, Column, DataTable, FormDialog, MoneyEdit, format_date

TYPE_LABELS = {"VEHICLE": "Vehicle", "PART": "Parts", "SERVICE": "Service"}
LOAN_LABELS = {"APPLIED": "Applied", "SANCTIONED": "Awaiting bank", "DISBURSED": "Received", "REJECTED": "Rejected"}


def open_invoice_pdf(conn: sqlite3.Connection, invoice_id: int) -> None:
    """(Re)create the invoice PDF so it shows the latest payments, then open it for printing."""
    widgets.open_file(write_invoice_pdf(conn, invoice_id))


def print_invoice(parent: QWidget, conn: sqlite3.Connection, invoice_id: int) -> bool:
    return widgets.print_pdf(parent, write_invoice_pdf(conn, invoice_id))


def offer_to_print(parent: QWidget, conn: sqlite3.Connection, invoice_id: int) -> None:
    """After a sale: say it's done and offer to print or view the invoice."""
    invoice_no = conn.execute("SELECT invoice_no FROM invoice WHERE id = ?", (invoice_id,)).fetchone()[0]
    choice = widgets.choose(parent, "Saved", f"Invoice {invoice_no} created.", ["Print", "View PDF", "Close"])
    if choice == "Print":
        print_invoice(parent, conn, invoice_id)
    elif choice == "View PDF":
        open_invoice_pdf(conn, invoice_id)


def _period(choice: str, today: date) -> tuple[date | None, date | None]:
    if choice == "TODAY":
        return today, today
    if choice == "MONTH":
        return today.replace(day=1), today
    if choice == "30DAYS":
        return today - timedelta(days=29), today
    if choice == "FY":
        start_year = int(financial_year(today)[:4])
        return date(start_year, 4, 1), today
    return None, None


class ReceivePaymentDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, invoice_id: int, refund: bool = False, parent: QWidget | None = None):
        super().__init__("Refund" if refund else "Receive payment", parent)
        self.conn, self.invoice_id, self.refund = conn, invoice_id, refund
        inv = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
        due = balance_due(conn, invoice_id)
        self.amount = MoneyEdit()
        self.amount.setValue(-due if refund else due)
        self.mode = ChoiceCombo(PAYMENT_MODE_CHOICES)
        self.reference = QLineEdit()
        self.date = QDateEdit(QDate.currentDate())
        self.date.setDisplayFormat("dd-MM-yyyy")
        self.date.setCalendarPopup(True)
        self.date.setMaximumDate(QDate.currentDate())
        self.date.setMinimumDate(QDate.fromString(inv["invoice_date"], "yyyy-MM-dd"))
        self.form.addRow("Invoice", QLabel(f"{inv['invoice_no']}  ·  {inv['bill_name']}"))
        self.form.addRow("Refund due" if refund else "Balance due", QLabel(format_inr(abs(due))))
        self.form.addRow("Amount *", self.amount)
        self.form.addRow("Refunded by" if refund else "Paid by", self.mode)
        self.form.addRow("Reference no.", self.reference)
        self.form.addRow("Date", self.date)

    def save(self) -> None:
        amount = self.amount.value()
        if not amount:
            raise BusinessError("Enter the amount.")
        record_payment(
            self.conn, invoice_id=self.invoice_id, amount=amount, mode=self.mode.value(),
            payment_type="REFUND" if self.refund else "BALANCE", reference_no=self.reference.text().strip() or None,
            on=self.date.date().toPython(),
        )


class LoanReceivedDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, loan_id: int, parent: QWidget | None = None):
        super().__init__("Loan amount received", parent)
        self.conn, self.loan_id = conn, loan_id
        loan = conn.execute(
            "SELECT l.*, f.name AS financer FROM loan l JOIN financer f ON f.id = l.financer_id WHERE l.id = ?",
            (loan_id,)).fetchone()
        self.reference = QLineEdit(loan["loan_account_no"] or "")
        self.reference.setPlaceholderText("NEFT / UTR reference")
        self.date = QDateEdit(QDate.currentDate())
        self.date.setDisplayFormat("dd-MM-yyyy")
        self.date.setCalendarPopup(True)
        self.date.setMaximumDate(QDate.currentDate())
        self.form.addRow("Financer", QLabel(loan["financer"]))
        self.form.addRow("Amount", QLabel(format_inr(loan["loan_amount"])))
        self.form.addRow("Reference", self.reference)
        self.form.addRow("Received on", self.date)

    def save(self) -> None:
        record_loan_disbursement(self.conn, self.loan_id, on=self.date.date().toPython(),
                                 reference_no=self.reference.text().strip() or None)


class CancelInvoiceDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, invoice_id: int, parent: QWidget | None = None):
        super().__init__("Cancel invoice", parent)
        self.conn, self.invoice_id = conn, invoice_id
        inv = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
        self.reason = QLineEdit()
        self.form.addRow("Invoice", QLabel(f"{inv['invoice_no']}  ·  {inv['bill_name']}  ·  "
                                           f"{format_inr(inv['grand_total'])}"))
        self.form.addRow("Reason *", self.reason)
        note = QLabel("The vehicle goes back into stock and parts are restocked. The invoice number is kept "
                      "and marked CANCELLED. Money received must then be refunded.")
        note.setWordWrap(True)
        note.setObjectName("hint")
        self.form.addRow(note)
        self.buttons.button(self.buttons.StandardButton.Save).setText("Cancel invoice")
        self.buttons.button(self.buttons.StandardButton.Cancel).setText("Keep invoice")

    def save(self) -> None:
        cancel_invoice(self.conn, self.invoice_id, self.reason.text())


class InvoicesPage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        title = QLabel("Invoices")
        title.setObjectName("pageTitle")

        self.print_button = QPushButton("Print")
        self.print_button.setObjectName("primary")
        self.print_button.clicked.connect(self.print_selected)
        self.open_button = QPushButton("View PDF")
        self.save_button = QPushButton("Save PDF as...")
        self.receive_button = QPushButton("Receive payment")
        self.loan_button = QPushButton("Loan received")
        self.refund_button = QPushButton("Refund")
        self.cancel_button = QPushButton("Cancel invoice")
        self.open_button.clicked.connect(self.open_pdf)
        self.save_button.clicked.connect(self.save_pdf_as)
        self.receive_button.clicked.connect(lambda: self._run(lambda c, i: ReceivePaymentDialog(c, i["id"], parent=self)))
        self.refund_button.clicked.connect(
            lambda: self._run(lambda c, i: ReceivePaymentDialog(c, i["id"], refund=True, parent=self)))
        self.loan_button.clicked.connect(lambda: self._run(lambda c, i: LoanReceivedDialog(c, i["loan_id"], self)))
        self.cancel_button.clicked.connect(lambda: self._run(lambda c, i: CancelInvoiceDialog(c, i["id"], self)))
        self.action_buttons = (self.print_button, self.open_button, self.save_button, self.receive_button, self.loan_button,
                               self.refund_button, self.cancel_button)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search invoice no., customer, mobile, chassis / engine / registration no.")
        self.search.setClearButtonEnabled(True)
        self.type_filter = ChoiceCombo([("All types", None), ("Vehicles", "VEHICLE"), ("Parts", "PART"),
                                        ("Service", "SERVICE")])
        self.status_filter = ChoiceCombo([("All", None), ("Balance due", "DUE"), ("Refund due", "REFUND"),
                                          ("Active", "ACTIVE"), ("Cancelled", "CANCELLED")])
        self.period = ChoiceCombo([("Last 30 days", "30DAYS"), ("Today", "TODAY"), ("This month", "MONTH"),
                                   ("This financial year", "FY"), ("All dates", "ALL")])
        self.search.textChanged.connect(self.refresh)
        for combo in (self.type_filter, self.status_filter, self.period):
            combo.currentIndexChanged.connect(self.refresh)

        self.table = DataTable([
            Column("Invoice no.", lambda r: r["invoice_no"]),
            Column("Date", lambda r: format_date(r["invoice_date"]), sort_key=lambda r: (r["invoice_date"], r["id"])),
            Column("Type", lambda r: TYPE_LABELS[r["invoice_type"]]),
            Column("Customer", lambda r: r["bill_name"]),
            Column("Mobile", lambda r: r["bill_phone"]),
            Column("Total", lambda r: format_inr(r["grand_total"]), align_right=True, sort_key=lambda r: r["grand_total"]),
            Column("Paid", lambda r: format_inr(r["paid"]), align_right=True, sort_key=lambda r: r["paid"]),
            Column("Balance", lambda r: format_inr(r["balance"]) if r["balance"] else "", align_right=True,
                   sort_key=lambda r: r["balance"]),
            Column("Loan", lambda r: LOAN_LABELS.get(r["loan_status"], "")),
            Column("Status", lambda r: "Cancelled" if r["status"] == "CANCELLED" else "Active"),
        ], highlight=lambda r: r["balance"] != 0)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(self.open_pdf)
        self.footer = QLabel()

        buttons = QHBoxLayout()
        for b in self.action_buttons:
            buttons.addWidget(b)
        buttons.addStretch()
        filters = QHBoxLayout()
        filters.addWidget(self.search, 3)
        filters.addWidget(self.type_filter, 1)
        filters.addWidget(self.status_filter, 1)
        filters.addWidget(self.period, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(buttons)
        layout.addLayout(filters)
        layout.addWidget(self.table)
        layout.addWidget(self.footer)
        self.refresh()

    def refresh(self) -> None:
        date_from, date_to = _period(self.period.value(), date.today())
        rows = find_invoices(
            self.conn, text=self.search.text(), invoice_type=self.type_filter.value(),
            status=self.status_filter.value(), date_from=date_from, date_to=date_to,
        )
        self.rows = {r["id"]: r for r in rows}
        self.table.fill(rows)
        self._update_buttons()
        active = [r for r in rows if r["status"] == "ACTIVE"]
        sales = sum(r["grand_total"] for r in active)
        due = sum(r["balance"] for r in rows if r["balance"] > 0)
        self.footer.setText(f"{len(rows)} invoices  ·  sales {format_inr(sales)}  ·  to collect {format_inr(due)}")

    def selected(self) -> sqlite3.Row | None:
        invoice_id = self.table.selected_id()
        return None if invoice_id is None else self.rows.get(invoice_id)

    def _update_buttons(self) -> None:
        inv = self.selected()
        for b in self.action_buttons:
            b.setEnabled(inv is not None)
        if inv is None:
            return
        active = inv["status"] == "ACTIVE"
        self.receive_button.setEnabled(active and inv["balance"] > 0)
        self.refund_button.setEnabled(inv["balance"] < 0)
        self.loan_button.setEnabled(active and inv["loan_status"] in ("APPLIED", "SANCTIONED"))
        self.cancel_button.setEnabled(active)

    def _run(self, make_dialog) -> None:
        inv = self.selected()
        if inv is not None and make_dialog(self.conn, inv).exec():
            self.refresh()
            self._select(inv["id"])

    def _select(self, invoice_id: int) -> None:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == invoice_id:
                self.table.selectRow(r)

    def print_selected(self) -> None:
        inv = self.selected()
        if inv is not None:
            print_invoice(self, self.conn, inv["id"])

    def open_pdf(self) -> None:
        inv = self.selected()
        if inv is not None:
            open_invoice_pdf(self.conn, inv["id"])

    def save_pdf_as(self) -> None:
        inv = self.selected()
        if inv is None:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Save invoice PDF", str(pdf_path(inv["invoice_no"]).name), "PDF files (*.pdf)")
        if target:
            write_invoice_pdf(self.conn, inv["id"], Path(target))
            widgets.show_info(self, f"Saved {target}")

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)
