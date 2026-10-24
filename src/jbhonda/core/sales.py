"""Selling vehicles and parts, cancelling invoices, and recording payments.

Every function here runs as one transaction: an invoice, its lines, the stock changes and
its number are all saved together or not at all.
"""

import sqlite3
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from jbhonda.core.customers import billing_snapshot
from jbhonda.core.errors import BusinessError
from jbhonda.core.gst import round_to_rupee, split_inclusive
from jbhonda.core.money import format_inr
from jbhonda.core.numbering import next_number
from jbhonda.db import NOW, transaction

WALK_IN_NAME = "Cash Customer"


@dataclass(frozen=True)
class PartLine:
    part_id: int
    quantity: int
    unit_price: int | None = None  # incl. GST; None = the part's current sale price
    discount: int = 0              # on the whole line, incl. GST


@dataclass(frozen=True)
class PaymentIn:
    mode: str
    amount: int
    reference_no: str | None = None


@dataclass
class _Line:
    item_kind: str
    vehicle_id: int | None
    part_id: int | None
    description: str
    hsn_code: str
    quantity: int
    unit_price: int
    discount: int
    gst_rate_bp: int
    unit_cost: int


# ---------------------------------------------------------------- selling

def sell_vehicle(
    conn: sqlite3.Connection, *, customer_id: int, vehicle_id: int, sale_price: int | None = None,
    discount: int = 0, accessories: Sequence[PartLine] = (), on: date | None = None,
    booking_id: int | None = None, hypothecated_to: str | None = None, notes: str | None = None,
) -> int:
    """Create a vehicle invoice (optionally with accessories). Returns the invoice id."""
    on = on or date.today()
    with transaction(conn):
        vehicle = conn.execute(
            "SELECT v.*, m.model_name, m.variant, m.hsn_code, m.gst_rate_bp, m.ex_showroom_price"
            " FROM vehicle v JOIN vehicle_model m ON m.id = v.vehicle_model_id WHERE v.id = ?",
            (vehicle_id,),
        ).fetchone()
        if vehicle is None:
            raise BusinessError("Vehicle not found.")
        if vehicle["status"] not in ("IN_STOCK", "BOOKED"):
            raise BusinessError(f"Vehicle {vehicle['chassis_number']} is not available for sale ({vehicle['status']}).")
        booking = _check_booking(conn, vehicle, customer_id, booking_id)

        price = vehicle["ex_showroom_price"] if sale_price is None else sale_price
        name = " ".join(p for p in ("Honda", vehicle["model_name"], vehicle["variant"]) if p)
        lines = [_Line(
            item_kind="VEHICLE", vehicle_id=vehicle_id, part_id=None,
            description=(
                f"{name} - {vehicle['color']}\n"
                f"Chassis No: {vehicle['chassis_number']}\nEngine No: {vehicle['engine_number']}"
            ),
            hsn_code=vehicle["hsn_code"], quantity=1, unit_price=price, discount=discount,
            gst_rate_bp=vehicle["gst_rate_bp"], unit_cost=vehicle["purchase_taxable_value"],
        )]
        lines += _part_lines(conn, accessories)

        invoice_id = _create_invoice(
            conn, "VEHICLE", on, _customer_snapshot(conn, customer_id), lines,
            customer_id=customer_id, booking_id=booking_id, hypothecated_to=hypothecated_to, notes=notes,
        )
        conn.execute(f"UPDATE vehicle SET status = 'SOLD', updated_at = {NOW} WHERE id = ?", (vehicle_id,))
        if booking is not None:
            _convert_booking(conn, booking, invoice_id, vehicle_id)
        return invoice_id


def _check_booking(
    conn: sqlite3.Connection, vehicle: sqlite3.Row, customer_id: int, booking_id: int | None,
) -> sqlite3.Row | None:
    if booking_id is None:
        if vehicle["status"] == "BOOKED":
            holder = conn.execute(
                "SELECT b.booking_no, c.name FROM booking b JOIN customer c ON c.id = b.customer_id"
                " WHERE b.vehicle_id = ? AND b.status = 'ALLOTTED'",
                (vehicle["id"],),
            ).fetchone()
            who = f" to booking {holder['booking_no']} ({holder['name']})" if holder else ""
            raise BusinessError(f"Vehicle {vehicle['chassis_number']} is allotted{who}. Sell it through that booking.")
        return None
    booking = conn.execute("SELECT * FROM booking WHERE id = ?", (booking_id,)).fetchone()
    if booking is None:
        raise BusinessError("Booking not found.")
    if booking["status"] not in ("OPEN", "ALLOTTED"):
        raise BusinessError(f"Booking {booking['booking_no']} is {booking['status'].lower()}.")
    if booking["customer_id"] != customer_id:
        raise BusinessError(f"Booking {booking['booking_no']} belongs to a different customer.")
    if vehicle["status"] == "BOOKED" and booking["vehicle_id"] != vehicle["id"]:
        raise BusinessError(f"Vehicle {vehicle['chassis_number']} is allotted to another booking.")
    return booking


def _convert_booking(conn: sqlite3.Connection, booking: sqlite3.Row, invoice_id: int, vehicle_id: int) -> None:
    """Mark the booking sold, release a different vehicle if one was allotted, move the advance to the invoice."""
    if booking["vehicle_id"] not in (None, vehicle_id):
        conn.execute(
            f"UPDATE vehicle SET status = 'IN_STOCK', updated_at = {NOW} WHERE id = ? AND status = 'BOOKED'",
            (booking["vehicle_id"],),
        )
    conn.execute(
        f"UPDATE booking SET status = 'CONVERTED', vehicle_id = ?, invoice_id = ?, updated_at = {NOW} WHERE id = ?",
        (vehicle_id, invoice_id, booking["id"]),
    )
    conn.execute(
        f"UPDATE payment SET invoice_id = ?, updated_at = {NOW} WHERE booking_id = ? AND invoice_id IS NULL",
        (invoice_id, booking["id"]),
    )
    if balance_due(conn, invoice_id) < 0:
        raise BusinessError("The booking advance is more than the bill amount. Check the price.")


def sell_parts(
    conn: sqlite3.Connection, *, lines: Sequence[PartLine], customer_id: int | None = None,
    walk_in_name: str = WALK_IN_NAME, walk_in_phone: str | None = None, on: date | None = None,
    notes: str | None = None,
) -> int:
    """Counter sale of parts. Without a customer_id it's billed to a walk-in name."""
    if not lines:
        raise BusinessError("Add at least one part to the bill.")
    on = on or date.today()
    with transaction(conn):
        if customer_id is not None:
            snapshot = _customer_snapshot(conn, customer_id)
        else:
            snapshot = {
                "bill_name": walk_in_name.strip() or WALK_IN_NAME, "bill_relation": None,
                "bill_phone": walk_in_phone, "bill_address": None, "bill_gstin": None,
            }
        return _create_invoice(
            conn, "PART", on, snapshot, _part_lines(conn, lines), customer_id=customer_id, notes=notes,
        )


def complete_part_sale(
    conn: sqlite3.Connection, *, lines: Sequence[PartLine], payments: Sequence[PaymentIn] = (),
    customer_id: int | None = None, walk_in_name: str = WALK_IN_NAME, walk_in_phone: str | None = None,
    on: date | None = None, notes: str | None = None,
) -> int:
    """Counter sale with the money taken: bill and payments saved together. Returns the invoice id.

    Paying less than the bill leaves a balance due (credit); paying more is refused.
    """
    on = on or date.today()
    with transaction(conn):
        invoice_id = sell_parts(conn, lines=lines, customer_id=customer_id, walk_in_name=walk_in_name,
                                walk_in_phone=walk_in_phone, on=on, notes=notes)
        due = balance_due(conn, invoice_id)
        paying = sum(p.amount for p in payments)
        if paying > due:
            raise BusinessError(f"Payment ({format_inr(paying)}) is more than the bill ({format_inr(due)}).")
        if customer_id is None and paying < due:
            raise BusinessError("A walk-in customer must pay the full amount. Pick a saved customer to give credit.")
        payment_type = "FULL_PAYMENT" if paying == due else "DOWN_PAYMENT"
        for p in payments:
            if p.mode == "FINANCE":
                raise BusinessError("Finance is only for vehicle sales.")
            record_payment(conn, invoice_id=invoice_id, amount=p.amount, mode=p.mode, payment_type=payment_type,
                           reference_no=p.reference_no, on=on)
        return invoice_id


def _customer_snapshot(conn: sqlite3.Connection, customer_id: int) -> dict[str, str | None]:
    customer = conn.execute("SELECT * FROM customer WHERE id = ?", (customer_id,)).fetchone()
    if customer is None:
        raise BusinessError("Customer not found.")
    return billing_snapshot(customer)


def _part_lines(conn: sqlite3.Connection, requested: Sequence[PartLine]) -> list[_Line]:
    wanted = Counter()
    for item in requested:
        if item.quantity <= 0:
            raise BusinessError("Quantity must be more than zero.")
        wanted[item.part_id] += item.quantity

    parts = {}
    for part_id, qty in wanted.items():
        part = conn.execute("SELECT * FROM part WHERE id = ?", (part_id,)).fetchone()
        if part is None:
            raise BusinessError("Part not found.")
        if not part["is_active"]:
            raise BusinessError(f"Part {part['part_number']} is no longer active.")
        if part["stock_qty"] < qty:
            raise BusinessError(
                f"Only {part['stock_qty']} of {part['part_number']} ({part['name']}) in stock; {qty} requested."
            )
        parts[part_id] = part

    for item in requested:
        mrp = parts[item.part_id]["mrp"]
        if mrp and item.unit_price is not None and item.unit_price > mrp:
            raise BusinessError(f"Price of {parts[item.part_id]['part_number']} cannot be more than its MRP.")

    return [
        _Line(
            item_kind="PART", vehicle_id=None, part_id=item.part_id,
            description=f"{parts[item.part_id]['name']} ({parts[item.part_id]['part_number']})",
            hsn_code=parts[item.part_id]["hsn_code"], quantity=item.quantity,
            unit_price=parts[item.part_id]["sale_price"] if item.unit_price is None else item.unit_price,
            discount=item.discount, gst_rate_bp=parts[item.part_id]["gst_rate_bp"],
            unit_cost=parts[item.part_id]["avg_cost"],
        )
        for item in requested
    ]


def _create_invoice(
    conn: sqlite3.Connection, invoice_type: str, on: date, snapshot: dict[str, str | None], lines: list[_Line],
    *, customer_id: int | None, booking_id: int | None = None, hypothecated_to: str | None = None,
    notes: str | None = None,
) -> int:
    taxed = []
    for line in lines:
        if line.unit_price < 0:
            raise BusinessError("Price cannot be negative.")
        if not 0 <= line.discount <= line.unit_price * line.quantity:
            raise BusinessError("Discount cannot be negative or more than the line amount.")
        taxed.append((line, split_inclusive(line.unit_price * line.quantity - line.discount, line.gst_rate_bp)))

    grand_total, round_off = round_to_rupee(sum(tax.gross for _, tax in taxed))
    invoice_no = next_number(conn, invoice_type, on)
    invoice_id = conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_type, invoice_date, customer_id, booking_id, bill_name,"
        " bill_relation, bill_phone, bill_address, bill_gstin, hypothecated_to, total_taxable, total_discount,"
        " total_cgst, total_sgst, round_off, grand_total, notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            invoice_no, invoice_type, on.isoformat(), customer_id, booking_id, snapshot["bill_name"],
            snapshot["bill_relation"], snapshot["bill_phone"], snapshot["bill_address"], snapshot["bill_gstin"],
            hypothecated_to, sum(t.taxable for _, t in taxed), sum(line.discount for line, _ in taxed),
            sum(t.cgst for _, t in taxed), sum(t.sgst for _, t in taxed), round_off, grand_total, notes,
        ),
    ).lastrowid

    for line_no, (line, tax) in enumerate(taxed, start=1):
        item_id = conn.execute(
            "INSERT INTO invoice_item (invoice_id, line_no, item_kind, vehicle_id, part_id, description, hsn_code,"
            " quantity, unit_price, discount, taxable_value, gst_rate_bp, cgst, sgst, line_total, unit_cost)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                invoice_id, line_no, line.item_kind, line.vehicle_id, line.part_id, line.description,
                line.hsn_code, line.quantity, line.unit_price, line.discount, tax.taxable, line.gst_rate_bp,
                tax.cgst, tax.sgst, tax.gross, line.unit_cost,
            ),
        ).lastrowid
        if line.part_id is not None:
            conn.execute(
                "INSERT INTO part_stock_movement (part_id, movement_type, quantity, unit_cost, reference_no,"
                " invoice_item_id, movement_date) VALUES (?, 'SALE', ?, ?, ?, ?, ?)",
                (line.part_id, -line.quantity, line.unit_cost, invoice_no, item_id, on.isoformat()),
            )
    return invoice_id


# ---------------------------------------------------------------- cancelling

def cancel_invoice(conn: sqlite3.Connection, invoice_id: int, reason: str, on: date | None = None) -> None:
    """Cancel an invoice: vehicles go back in stock and parts are restocked.

    Payments already received stay on record; return money with a REFUND payment. If the sale
    came from a booking, the booking re-opens and its advance moves back to it.
    """
    if not reason.strip():
        raise BusinessError("Give a reason for cancelling the invoice.")
    on = on or date.today()
    with transaction(conn):
        invoice = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
        if invoice is None:
            raise BusinessError("Invoice not found.")
        if invoice["status"] == "CANCELLED":
            raise BusinessError(f"Invoice {invoice['invoice_no']} is already cancelled.")

        conn.execute(
            f"UPDATE invoice SET status = 'CANCELLED', cancelled_at = {NOW}, cancel_reason = ?, updated_at = {NOW}"
            " WHERE id = ?",
            (reason.strip(), invoice_id),
        )
        if invoice["booking_id"] is not None:
            # The booking is live again; its advance goes back to it rather than staying on a dead invoice.
            conn.execute(
                f"UPDATE booking SET status = 'OPEN', vehicle_id = NULL, invoice_id = NULL, updated_at = {NOW}"
                " WHERE id = ?",
                (invoice["booking_id"],),
            )
            conn.execute(
                f"UPDATE payment SET invoice_id = NULL, updated_at = {NOW} WHERE invoice_id = ? AND booking_id = ?",
                (invoice_id, invoice["booking_id"]),
            )
        for item in conn.execute("SELECT * FROM invoice_item WHERE invoice_id = ?", (invoice_id,)).fetchall():
            if item["vehicle_id"] is not None:
                conn.execute(
                    f"UPDATE vehicle SET status = 'IN_STOCK', updated_at = {NOW} WHERE id = ? AND status = 'SOLD'",
                    (item["vehicle_id"],),
                )
            else:
                conn.execute(
                    "INSERT INTO part_stock_movement (part_id, movement_type, quantity, unit_cost, reference_no,"
                    " invoice_item_id, movement_date, notes) VALUES (?, 'SALE_CANCEL', ?, ?, ?, ?, ?, ?)",
                    (item["part_id"], item["quantity"], item["unit_cost"], invoice["invoice_no"], item["id"],
                     on.isoformat(), reason.strip()),
                )


# ---------------------------------------------------------------- payments

PAYMENT_MODES = ("CASH", "UPI", "CARD", "CHEQUE", "BANK_TRANSFER", "FINANCE")


def amount_paid(conn: sqlite3.Connection, invoice_id: int) -> int:
    return conn.execute(
        "SELECT coalesce(sum(amount), 0) FROM payment WHERE invoice_id = ?", (invoice_id,)
    ).fetchone()[0]


def balance_due(conn: sqlite3.Connection, invoice_id: int) -> int:
    """Amount still owed. Negative means the customer has overpaid (refund due)."""
    row = conn.execute("SELECT grand_total, status FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
    if row is None:
        raise BusinessError("Invoice not found.")
    owed = row["grand_total"] if row["status"] == "ACTIVE" else 0
    return owed - amount_paid(conn, invoice_id)


def record_payment(
    conn: sqlite3.Connection, *, invoice_id: int, amount: int, mode: str, payment_type: str = "FULL_PAYMENT",
    on: date | None = None, reference_no: str | None = None, loan_id: int | None = None, notes: str | None = None,
) -> str:
    """Record money received against an invoice. Returns the receipt number.

    For a refund pass payment_type='REFUND' and a positive amount; it is stored as negative.
    """
    if mode not in PAYMENT_MODES:
        raise BusinessError(f"Unknown payment mode {mode}.")
    if amount <= 0:
        raise BusinessError("Amount must be more than zero.")
    if mode == "FINANCE" and loan_id is None:
        raise BusinessError("A finance payment must be linked to a loan.")
    if mode in ("UPI", "CARD", "CHEQUE", "BANK_TRANSFER") and not (reference_no or "").strip():
        raise BusinessError("Enter the transaction / cheque reference number.")
    on = on or date.today()
    with transaction(conn):
        invoice = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
        if invoice is None:
            raise BusinessError("Invoice not found.")
        due = balance_due(conn, invoice_id)
        if payment_type == "REFUND":
            if amount > -due:
                raise BusinessError(f"Refund is more than the amount overpaid ({format_inr(max(-due, 0))}).")
            amount = -amount
        else:
            if invoice["status"] == "CANCELLED":
                raise BusinessError(f"Invoice {invoice['invoice_no']} is cancelled; it can only receive refunds.")
            if amount > due:
                raise BusinessError(f"Amount is more than the balance due ({format_inr(due)}).")
        receipt_no = next_number(conn, "RECEIPT", on)
        conn.execute(
            "INSERT INTO payment (receipt_no, customer_id, invoice_id, payment_date, payment_type, payment_mode,"
            " amount, reference_no, loan_id, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (receipt_no, invoice["customer_id"], invoice_id, on.isoformat(), payment_type, mode, amount,
             reference_no, loan_id, notes),
        )
        return receipt_no
