"""Sales figures with filters: period, vehicle/parts/service, loan or direct, payment mode."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox, QDateEdit, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from jbhonda.core.money import format_inr
from jbhonda.core.numbering import financial_year
from jbhonda.core.sales_report import INVOICE_TYPES, PAYMENT_MODES, Filters, sales_report
from jbhonda.reports.export import DATE, MONEY, TEXT, Report, write_xlsx
from jbhonda.ui import widgets
from jbhonda.ui.home_page import StatCard
from jbhonda.ui.widgets import ChoiceCombo, Column, DataTable, format_date

TYPE_LABELS = {"VEHICLE": "Vehicles", "PART": "Parts", "SERVICE": "Servicing"}
MODE_LABELS = {"CASH": "Cash", "UPI": "UPI", "CARD": "Card", "CHEQUE": "Cheque", "BANK_TRANSFER": "Bank transfer",
               "FINANCE": "Finance (loan)"}


def period_dates(choice: str, today: date) -> tuple[date, date] | None:
    if choice == "TODAY":
        return today, today
    if choice == "MONTH":
        return today.replace(day=1), today
    if choice == "LAST_MONTH":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if choice == "FY":
        return date(int(financial_year(today)[:4]), 4, 1), today
    return None


def _choose_save_path(parent: QWidget, suggested: str) -> Path | None:
    path, _ = QFileDialog.getSaveFileName(parent, "Save Excel file", str(Path.home() / suggested),
                                          "Excel files (*.xlsx)")
    return Path(path) if path else None


class SalesReportPage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        title = QLabel("Sales report")
        title.setObjectName("pageTitle")

        # -- filters
        self.period = ChoiceCombo([("This month", "MONTH"), ("Today", "TODAY"), ("Last month", "LAST_MONTH"),
                                   ("This financial year", "FY"), ("Custom dates", "CUSTOM"), ("All dates", "ALL")])
        today = QDate.currentDate()
        self.date_from = QDateEdit(QDate(today.year(), today.month(), 1))
        self.date_to = QDateEdit(today)
        for d in (self.date_from, self.date_to):
            d.setDisplayFormat("dd-MM-yyyy")
            d.setCalendarPopup(True)
            d.dateChanged.connect(self.refresh)
        self.period.currentIndexChanged.connect(self._period_changed)
        self.type_boxes = {t: QCheckBox(TYPE_LABELS[t]) for t in INVOICE_TYPES}
        self.finance = ChoiceCombo([("Loan or direct", None), ("Through loan", "LOAN"), ("Direct (no loan)", "DIRECT")])
        self.all_modes = QCheckBox("All payment modes")
        self.mode_boxes = {m: QCheckBox(MODE_LABELS[m]) for m in PAYMENT_MODES}
        for box in (*self.type_boxes.values(), *self.mode_boxes.values()):
            box.setChecked(True)
            box.toggled.connect(self.refresh)
        self.all_modes.setChecked(True)
        self.all_modes.toggled.connect(self._all_modes_toggled)
        self.finance.currentIndexChanged.connect(self.refresh)
        self.export_button = QPushButton("Export to Excel")
        self.export_button.clicked.connect(self.export)

        filters = QGroupBox("Filters")
        grid = QGridLayout(filters)
        dates = QHBoxLayout()
        for w in (self.period, QLabel("from"), self.date_from, QLabel("to"), self.date_to):
            dates.addWidget(w)
        dates.addStretch()
        grid.addWidget(QLabel("Period"), 0, 0)
        grid.addLayout(dates, 0, 1)
        types = QHBoxLayout()
        for box in self.type_boxes.values():
            types.addWidget(box)
        types.addSpacing(24)
        types.addWidget(self.finance)
        types.addStretch()
        grid.addWidget(QLabel("Sales of"), 1, 0)
        grid.addLayout(types, 1, 1)
        modes = QHBoxLayout()
        modes.addWidget(self.all_modes)
        for box in self.mode_boxes.values():
            modes.addWidget(box)
        modes.addStretch()
        modes.addWidget(self.export_button)
        grid.addWidget(QLabel("Paid by"), 2, 0)
        grid.addLayout(modes, 2, 1)

        # -- figures
        self.cards = {
            "count": StatCard("Invoices"),
            "sale_value": StatCard("Sale value"),
            "received": StatCard("Received"),
            "outstanding": StatCard("Still to collect"),
            "taxable": StatCard("Taxable value"),
            "gst": StatCard("GST (CGST + SGST)"),
        }
        cards = QGridLayout()
        for i, card in enumerate(self.cards.values()):
            cards.addWidget(card, 0, i)
        self.note = QLabel()
        self.note.setObjectName("cardCaption")
        self.note.setWordWrap(True)

        self.by_type = DataTable([
            Column("Sales of", lambda r: TYPE_LABELS[r[0]]),
            Column("Invoices", lambda r: r[1]["count"], align_right=True),
            Column("Value", lambda r: format_inr(r[1]["value"]), align_right=True),
            Column("Received", lambda r: format_inr(r[1]["received"]), align_right=True),
        ])
        self.by_mode = DataTable([
            Column("Paid by", lambda r: MODE_LABELS[r[0]]),
            Column("Amount", lambda r: format_inr(r[1]), align_right=True, sort_key=lambda r: r[1]),
        ])
        for t in (self.by_type, self.by_mode):
            t.setMaximumHeight(170)
        self.table = DataTable([
            Column("Invoice no.", lambda r: r["invoice_no"]),
            Column("Date", lambda r: format_date(r["invoice_date"]), sort_key=lambda r: (r["invoice_date"], r["id"])),
            Column("Type", lambda r: TYPE_LABELS[r["invoice_type"]]),
            Column("Customer", lambda r: r["bill_name"]),
            Column("Value", lambda r: format_inr(r["grand_total"]), align_right=True, sort_key=lambda r: r["grand_total"]),
            Column("Received", lambda r: format_inr(r["received"]), align_right=True, sort_key=lambda r: r["received"]),
            Column("In selected modes", lambda r: format_inr(r["received_in_modes"]), align_right=True,
                   sort_key=lambda r: r["received_in_modes"]),
            Column("Loan", lambda r: "Yes" if r["has_loan"] else ""),
            Column("Paid by", lambda r: ", ".join(MODE_LABELS[m] for m in (r["modes"] or "").split(",") if m)),
        ])

        breakdown = QHBoxLayout()
        breakdown.addWidget(self.by_type, 3)
        breakdown.addWidget(self.by_mode, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(filters)
        layout.addLayout(cards)
        layout.addWidget(self.note)
        layout.addLayout(breakdown)
        layout.addWidget(self.table, 1)
        self._period_changed()

    # ------------------------------------------------------------ filters

    def _period_changed(self) -> None:
        choice = self.period.value()
        custom = choice == "CUSTOM"
        self.date_from.setEnabled(custom)
        self.date_to.setEnabled(custom)
        dates = period_dates(choice, date.today())
        if dates:
            for edit, d in zip((self.date_from, self.date_to), dates):
                edit.blockSignals(True)
                edit.setDate(QDate(d.year, d.month, d.day))
                edit.blockSignals(False)
        self.refresh()

    def _all_modes_toggled(self, on: bool) -> None:
        for box in self.mode_boxes.values():
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)
        self.refresh()

    def filters(self) -> Filters:
        all_dates = self.period.value() == "ALL"
        modes = [m for m, box in self.mode_boxes.items() if box.isChecked()]
        return Filters(
            date_from=None if all_dates else self.date_from.date().toPython(),
            date_to=None if all_dates else self.date_to.date().toPython(),
            types=[t for t, box in self.type_boxes.items() if box.isChecked()],
            finance=self.finance.value(),
            modes=None if len(modes) == len(PAYMENT_MODES) else modes,
        )

    # ------------------------------------------------------------ results

    def refresh(self) -> None:
        f = self.filters()
        self.report = r = sales_report(self.conn, f)
        mode_filtered = f.modes is not None
        values = {"count": str(r.count), "sale_value": format_inr(r.sale_value),
                  "received": format_inr(r.received_in_modes if mode_filtered else r.received),
                  "outstanding": format_inr(r.outstanding), "taxable": format_inr(r.taxable), "gst": format_inr(r.gst)}
        for key, text in values.items():
            self.cards[key].value.setText(text)
        self.cards["received"].findChildren(QLabel)[1].setText(
            "Received in selected modes" if mode_filtered else "Received")
        self.note.setText(
            "Only invoices paid at least partly in the selected modes are listed. 'Received' counts just those "
            "modes, so a bill paid part cash and part UPI is never counted twice." if mode_filtered else
            "Sales are counted by invoice date. Cancelled invoices are left out.")
        self.by_type.fill(list(r.by_type.items()), row_id=lambda row: 0)
        self.by_mode.fill(list(r.by_mode.items()), row_id=lambda row: 0)
        self.table.fill(r.invoices)
        self.table.setColumnHidden(6, not mode_filtered)

    def export(self) -> Path | None:
        f = self.filters()
        span = f"{f.date_from:%d-%m-%Y} to {f.date_to:%d-%m-%Y}" if f.date_from else "all dates"
        target = _choose_save_path(self, f"Sales report {span}.xlsx")
        if target is None:
            return None
        r = self.report
        summary = Report("Summary", [("Item", TEXT), ("Value", TEXT)], [
            ("Period", span),
            ("Sales of", ", ".join(TYPE_LABELS[t] for t in f.types)),
            ("Finance", self.finance.currentText()),
            ("Paid by", "All modes" if f.modes is None else ", ".join(MODE_LABELS[m] for m in f.modes)),
            ("Invoices", str(r.count)),
            ("Sale value", format_inr(r.sale_value)),
            ("Received", format_inr(r.received_in_modes if f.modes is not None else r.received)),
            ("Still to collect", format_inr(r.outstanding)),
            ("Taxable value", format_inr(r.taxable)),
            ("GST", format_inr(r.gst)),
        ])
        detail = Report("Invoices", [
            ("Invoice no", TEXT), ("Date", DATE), ("Type", TEXT), ("Customer", TEXT), ("Value", MONEY),
            ("Taxable", MONEY), ("GST", MONEY), ("Received", MONEY), ("In selected modes", MONEY), ("Loan", TEXT),
        ], [(i["invoice_no"], i["invoice_date"], TYPE_LABELS[i["invoice_type"]], i["bill_name"], i["grand_total"],
             i["total_taxable"], i["gst"], i["received"], i["received_in_modes"], "Yes" if i["has_loan"] else "")
            for i in r.invoices])
        by_mode = Report("By payment mode", [("Paid by", TEXT), ("Amount", MONEY)],
                         [(MODE_LABELS[m], a) for m, a in r.by_mode.items()])
        try:
            write_xlsx(target, [summary, detail, by_mode], subtitle=span)
        except OSError as exc:
            widgets.show_error(self, f"Could not save {target}: {exc}")
            return None
        widgets.open_file(target)
        return target

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)
