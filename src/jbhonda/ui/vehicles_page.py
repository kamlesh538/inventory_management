"""Vehicle stock: add vehicles as they arrive from Honda, and search/filter the list."""

import sqlite3
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import VEHICLE_STATUSES, find_vehicles, save_vehicle, vehicle_colors
from jbhonda.core.money import div_round, format_inr
from jbhonda.ui import widgets
from jbhonda.ui.widgets import ChoiceCombo, Column, DataTable, FormDialog, MoneyEdit, format_date

STATUS_LABELS = {
    "IN_STOCK": "In stock",
    "BOOKED": "Booked",
    "SOLD": "Sold",
    "RETURNED_TO_OEM": "Returned to Honda",
}


def model_label(row) -> str:
    return f"{row['model_name']} {row['variant']}".strip()


class VehicleDialog(FormDialog):
    def __init__(self, conn: sqlite3.Connection, vehicle_id: int | None = None, parent: QWidget | None = None):
        super().__init__("Vehicle details" if vehicle_id else "Add vehicle", parent)
        self.setMinimumWidth(480)
        self.conn, self.vehicle_id = conn, vehicle_id
        self.saved_count = 0
        existing = conn.execute("SELECT * FROM vehicle WHERE id = ?", (vehicle_id,)).fetchone() if vehicle_id else None

        models = conn.execute(
            "SELECT * FROM vehicle_model WHERE is_active = 1 OR id = ? ORDER BY model_name, variant",
            (existing["vehicle_model_id"] if existing else None,),
        ).fetchall()
        self.gst_rate_by_model = {m["id"]: m["gst_rate_bp"] for m in models}
        self.model = ChoiceCombo([(model_label(m), m["id"]) for m in models])
        self.chassis = QLineEdit()
        self.engine = QLineEdit()
        self.color = QComboBox()
        self.color.setEditable(True)
        self.color.addItems(vehicle_colors(conn))
        self.color.setCurrentText("")
        self.key = QLineEdit()
        self.battery = QLineEdit()
        self.mfg_month = QDateEdit(QDate.currentDate())
        self.mfg_month.setDisplayFormat("MMM yyyy")
        self.purchase_invoice = QLineEdit()
        self.purchase_invoice.setPlaceholderText("Honda's invoice number to the dealer")
        self.purchase_date = QDateEdit(QDate.currentDate())
        self.purchase_date.setDisplayFormat("dd-MM-yyyy")
        self.purchase_date.setCalendarPopup(True)
        self.purchase_date.setMaximumDate(QDate.currentDate())
        self.taxable = MoneyEdit()
        self.gst = MoneyEdit()
        self.total = QLabel("₹0.00")
        self.registration = QLineEdit()
        self.notes = QLineEdit()
        self.status_note = QLabel()
        self.status_note.setWordWrap(True)
        self.status_note.setObjectName("hint")

        for widget in (self.chassis, self.engine, self.key, self.battery, self.registration):
            widget.textEdited.connect(lambda text, w=widget: self._upper(w))
        self.taxable.textEdited.connect(self._fill_gst)
        self.model.currentIndexChanged.connect(self._fill_gst)
        self.taxable.textChanged.connect(self._update_total)
        self.gst.textChanged.connect(self._update_total)

        self.form.addRow(self.status_note)
        self.form.addRow("Model *", self.model)
        self.form.addRow("Chassis number *", self.chassis)
        self.form.addRow("Engine number *", self.engine)
        self.form.addRow("Colour *", self.color)
        self.form.addRow("Key number", self.key)
        self.form.addRow("Battery number", self.battery)
        self.form.addRow("Manufacturing month", self.mfg_month)
        self.form.addRow("Purchase invoice no.", self.purchase_invoice)
        self.form.addRow("Purchase date", self.purchase_date)
        self.form.addRow("Purchase price (excl. GST)", self.taxable)
        self.form.addRow("Purchase GST", self.gst)
        self.form.addRow("Total purchase cost", self.total)
        self.form.addRow("Registration number", self.registration)
        self.form.addRow("Notes", self.notes)
        self.status_note.hide()
        # Date pickers can't be blank; remember which dates were blank so saving keeps them blank.
        self._blank_dates: dict[QDateEdit, QDate] = {}

        if existing:
            self._load(existing)
        else:
            self.add_another = self.buttons.addButton("Save && add another", QDialogButtonBox.ButtonRole.ActionRole)
            self.add_another.clicked.connect(self.save_and_add_another)
            self.registration.setEnabled(False)  # only known after sale
        if not models:
            self.status_note.setText("Add a vehicle model under Masters first.")
            self.status_note.show()

    def _load(self, v: sqlite3.Row) -> None:
        self.model.setValue(v["vehicle_model_id"])
        self.chassis.setText(v["chassis_number"])
        self.engine.setText(v["engine_number"])
        self.color.setCurrentText(v["color"])
        self.key.setText(v["key_number"] or "")
        self.battery.setText(v["battery_number"] or "")
        if v["manufacturing_month"]:
            self.mfg_month.setDate(QDate.fromString(v["manufacturing_month"], "yyyy-MM"))
        else:
            self._blank_dates[self.mfg_month] = self.mfg_month.date()
        self.purchase_invoice.setText(v["purchase_invoice_no"] or "")
        if v["purchase_date"]:
            self.purchase_date.setDate(QDate.fromString(v["purchase_date"], "yyyy-MM-dd"))
        else:
            self._blank_dates[self.purchase_date] = self.purchase_date.date()
        self.taxable.setValue(v["purchase_taxable_value"])
        self.gst.setValue(v["purchase_gst_amount"])
        self.registration.setText(v["registration_number"] or "")
        self.notes.setText(v["notes"] or "")
        if v["status"] == "SOLD":
            for widget in (self.model, self.chassis, self.engine, self.color, self.key, self.battery, self.mfg_month,
                           self.purchase_invoice, self.purchase_date, self.taxable, self.gst):
                widget.setEnabled(False)
            self.status_note.setText(
                "This vehicle is sold. Only the registration number and notes can be changed."
            )
            self.status_note.show()

    @staticmethod
    def _upper(edit: QLineEdit) -> None:
        pos = edit.cursorPosition()
        edit.setText(edit.text().upper())
        edit.setCursorPosition(pos)

    def _fill_gst(self) -> None:
        """Work out purchase GST from the model's rate; the user can still overwrite it."""
        try:
            taxable = self.taxable.value()
        except ValueError:
            return
        rate = self.gst_rate_by_model.get(self.model.value())
        if taxable is not None and rate is not None:
            self.gst.setValue(div_round(taxable * rate, 10000))

    def _update_total(self) -> None:
        try:
            total = (self.taxable.value() or 0) + (self.gst.value() or 0)
        except ValueError:
            return
        self.total.setText(format_inr(total))

    def _date(self, edit: QDateEdit) -> QDate | None:
        """The picked date, or None if it was blank on load and hasn't been touched."""
        return None if self._blank_dates.get(edit) == edit.date() else edit.date()

    def save(self) -> None:
        if self.model.value() is None:
            raise BusinessError("Choose a vehicle model. Add models under Masters.")
        mfg_month = self._date(self.mfg_month)
        purchase_date = self._date(self.purchase_date)
        self.vehicle_id = save_vehicle(
            self.conn, vehicle_id=self.vehicle_id, vehicle_model_id=self.model.value(),
            chassis_number=self.chassis.text(), engine_number=self.engine.text(), color=self.color.currentText(),
            key_number=self.key.text(), battery_number=self.battery.text(),
            manufacturing_month=mfg_month.toString("yyyy-MM") if mfg_month else None,
            purchase_invoice_no=self.purchase_invoice.text(),
            purchase_date=purchase_date.toPython() if purchase_date else None,
            purchase_taxable_value=self.taxable.value() or 0, purchase_gst_amount=self.gst.value() or 0,
            registration_number=self.registration.text(), notes=self.notes.text(),
        )
        self.saved_count += 1

    def save_and_add_another(self) -> None:
        """Keep model, colour and purchase details for the next vehicle off the same truck."""
        if not self.try_save():
            return
        saved = self.chassis.text()
        self.vehicle_id = None
        for widget in (self.chassis, self.engine, self.key, self.battery):
            widget.clear()
        self.status_note.setText(f"Saved {saved}. Enter the next vehicle ({self.saved_count} added so far).")
        self.status_note.show()
        self.chassis.setFocus()


class VehiclesPage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        title = QLabel("Vehicles")
        title.setObjectName("pageTitle")

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search chassis, engine, key, registration no., model or customer")
        self.search.setClearButtonEnabled(True)
        self.model_filter = ChoiceCombo([("All models", None)])
        self.color_filter = ChoiceCombo([("All colours", None)])
        self.status_filter = ChoiceCombo([("All statuses", None), *((STATUS_LABELS[s], s) for s in VEHICLE_STATUSES)])
        self.add_button = QPushButton("Add vehicle")
        self.add_button.setObjectName("primary")
        self.open_button = QPushButton("Open")

        self.search.textChanged.connect(self.refresh)
        for combo in (self.model_filter, self.color_filter, self.status_filter):
            combo.currentIndexChanged.connect(self.refresh)
        self.add_button.clicked.connect(self.add)
        self.open_button.clicked.connect(self.open_selected)

        self.table = DataTable([
            Column("Model", model_label),
            Column("Colour", lambda r: r["color"]),
            Column("Chassis no.", lambda r: r["chassis_number"]),
            Column("Engine no.", lambda r: r["engine_number"]),
            Column("Purchased", lambda r: format_date(r["purchase_date"]), sort_key=lambda r: r["purchase_date"]),
            Column("Cost", lambda r: format_inr(r["purchase_taxable_value"] + r["purchase_gst_amount"]),
                   align_right=True, sort_key=lambda r: r["purchase_taxable_value"] + r["purchase_gst_amount"]),
            Column("Status", lambda r: STATUS_LABELS[r["status"]]),
            Column("Invoice", lambda r: r["invoice_no"]),
            Column("Sold on", lambda r: format_date(r["invoice_date"]), sort_key=lambda r: r["invoice_date"]),
            Column("Customer", lambda r: r["bill_name"]),
        ])
        self.table.doubleClicked.connect(self.open_selected)
        self.table.itemSelectionChanged.connect(lambda: self.open_button.setEnabled(self.table.selected_id() is not None))
        self.footer = QLabel()

        filters = QHBoxLayout()
        filters.addWidget(self.search, 3)
        filters.addWidget(self.model_filter, 1)
        filters.addWidget(self.color_filter, 1)
        filters.addWidget(self.status_filter, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(buttons)
        layout.addLayout(filters)
        layout.addWidget(self.table)
        layout.addWidget(self.footer)
        self.reload_filters()

    def reload_filters(self) -> None:
        """Refresh the model and colour lists (models may have been added in Masters)."""
        models = self.conn.execute(
            "SELECT * FROM vehicle_model WHERE is_active = 1 OR id IN (SELECT vehicle_model_id FROM vehicle)"
            " ORDER BY model_name, variant"
        ).fetchall()
        for combo, items in (
            (self.model_filter, [(model_label(m), m["id"]) for m in models]),
            (self.color_filter, [(c, c) for c in vehicle_colors(self.conn)]),
        ):
            current = combo.value()
            combo.blockSignals(True)
            while combo.count() > 1:
                combo.removeItem(1)
            for label, value in items:
                combo.addItem(label, value)
            combo.setValue(current)
            combo.blockSignals(False)
        self.refresh()

    def refresh(self) -> None:
        rows = find_vehicles(
            self.conn, text=self.search.text(), vehicle_model_id=self.model_filter.value(),
            color=self.color_filter.value(), status=self.status_filter.value(),
        )
        self.table.fill(rows)
        self.open_button.setEnabled(False)
        in_stock = [r for r in rows if r["status"] == "IN_STOCK"]
        stock_value = sum(r["purchase_taxable_value"] + r["purchase_gst_amount"] for r in in_stock)
        self.footer.setText(
            f"{len(rows)} vehicles shown  ·  {len(in_stock)} in stock  ·  stock value {format_inr(stock_value)}"
        )

    def add(self) -> None:
        dialog = VehicleDialog(self.conn, parent=self)
        dialog.exec()
        if dialog.saved_count:
            self.reload_filters()

    def open_selected(self) -> None:
        vehicle_id = self.table.selected_id()
        if vehicle_id is not None and VehicleDialog(self.conn, vehicle_id, self).exec():
            self.reload_filters()

    def showEvent(self, event) -> None:
        self.reload_filters()
        super().showEvent(event)
