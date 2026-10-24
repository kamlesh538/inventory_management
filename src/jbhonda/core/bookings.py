"""Bookings: a customer pays an advance for a model/colour; a vehicle is allotted later; then it's sold.

The advance is held as payments linked to the booking. When the booking is converted to a
sale (sell_vehicle with booking_id), those payments move onto the invoice automatically.
"""

import sqlite3
from datetime import date

from jbhonda.core.errors import BusinessError
from jbhonda.core.money import format_inr
from jbhonda.core.numbering import next_number
from jbhonda.db import NOW, transaction

PAYMENT_MODES = ("CASH", "UPI", "CARD", "CHEQUE", "BANK_TRANSFER")
OPEN_STATUSES = ("OPEN", "ALLOTTED")


def _booking(conn: sqlite3.Connection, booking_id: int) -> sqlite3.Row:
    booking = conn.execute("SELECT * FROM booking WHERE id = ?", (booking_id,)).fetchone()
    if booking is None:
        raise BusinessError("Booking not found.")
    return booking


def _require_open(booking: sqlite3.Row) -> None:
    if booking["status"] not in OPEN_STATUSES:
        raise BusinessError(f"Booking {booking['booking_no']} is {booking['status'].lower()}.")


def advance_held(conn: sqlite3.Connection, booking_id: int) -> int:
    """Advance paid on a booking that hasn't yet moved to an invoice, net of refunds."""
    return conn.execute(
        "SELECT coalesce(sum(amount), 0) FROM payment WHERE booking_id = ? AND invoice_id IS NULL", (booking_id,)
    ).fetchone()[0]


def _record(
    conn: sqlite3.Connection, booking: sqlite3.Row, *, amount: int, mode: str, payment_type: str,
    reference_no: str | None, on: date,
) -> str:
    if mode not in PAYMENT_MODES:
        raise BusinessError(f"Unknown payment mode {mode}.")
    if mode != "CASH" and not (reference_no or "").strip():
        raise BusinessError("Enter the transaction / cheque reference number.")
    receipt_no = next_number(conn, "RECEIPT", on)
    conn.execute(
        "INSERT INTO payment (receipt_no, customer_id, booking_id, payment_date, payment_type, payment_mode,"
        " amount, reference_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (receipt_no, booking["customer_id"], booking["id"], on.isoformat(), payment_type, mode, amount,
         (reference_no or "").strip() or None),
    )
    return receipt_no


def create_booking(
    conn: sqlite3.Connection, *, customer_id: int, vehicle_model_id: int, preferred_color: str | None = None,
    quoted_price: int = 0, expected_delivery_date: date | None = None, advance: int = 0,
    advance_mode: str = "CASH", reference_no: str | None = None, on: date | None = None,
) -> int:
    on = on or date.today()
    if advance < 0 or quoted_price < 0:
        raise BusinessError("Amounts cannot be negative.")
    if quoted_price and advance > quoted_price:
        raise BusinessError("Advance cannot be more than the quoted price.")
    if expected_delivery_date and expected_delivery_date < on:
        raise BusinessError("Expected delivery date cannot be before the booking date.")
    with transaction(conn):
        if conn.execute("SELECT 1 FROM customer WHERE id = ?", (customer_id,)).fetchone() is None:
            raise BusinessError("Choose a customer.")
        model = conn.execute("SELECT is_active FROM vehicle_model WHERE id = ?", (vehicle_model_id,)).fetchone()
        if model is None or not model["is_active"]:
            raise BusinessError("Choose an active vehicle model.")
        booking_id = conn.execute(
            "INSERT INTO booking (booking_no, customer_id, vehicle_model_id, preferred_color, booking_date,"
            " expected_delivery_date, quoted_price) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                next_number(conn, "BOOKING", on), customer_id, vehicle_model_id, (preferred_color or "").strip() or None,
                on.isoformat(), expected_delivery_date.isoformat() if expected_delivery_date else None, quoted_price,
            ),
        ).lastrowid
        if advance:
            _record(conn, _booking(conn, booking_id), amount=advance, mode=advance_mode,
                    payment_type="BOOKING_ADVANCE", reference_no=reference_no, on=on)
        return booking_id


def add_advance(
    conn: sqlite3.Connection, booking_id: int, *, amount: int, mode: str, reference_no: str | None = None,
    on: date | None = None,
) -> str:
    if amount <= 0:
        raise BusinessError("Amount must be more than zero.")
    with transaction(conn):
        booking = _booking(conn, booking_id)
        _require_open(booking)
        return _record(conn, booking, amount=amount, mode=mode, payment_type="BOOKING_ADVANCE",
                       reference_no=reference_no, on=on or date.today())


def allot_vehicle(conn: sqlite3.Connection, booking_id: int, vehicle_id: int) -> None:
    """Reserve a specific in-stock vehicle for the booking (releasing any vehicle allotted before)."""
    with transaction(conn):
        booking = _booking(conn, booking_id)
        _require_open(booking)
        vehicle = conn.execute("SELECT * FROM vehicle WHERE id = ?", (vehicle_id,)).fetchone()
        if vehicle is None:
            raise BusinessError("Vehicle not found.")
        if vehicle["id"] == booking["vehicle_id"]:
            return
        if vehicle["status"] != "IN_STOCK":
            raise BusinessError(f"Vehicle {vehicle['chassis_number']} is not in stock.")
        if vehicle["vehicle_model_id"] != booking["vehicle_model_id"]:
            raise BusinessError("That vehicle is a different model from the one booked.")
        _release_vehicle(conn, booking)
        conn.execute(f"UPDATE vehicle SET status = 'BOOKED', updated_at = {NOW} WHERE id = ?", (vehicle_id,))
        conn.execute(
            f"UPDATE booking SET vehicle_id = ?, status = 'ALLOTTED', updated_at = {NOW} WHERE id = ?",
            (vehicle_id, booking_id),
        )


def _release_vehicle(conn: sqlite3.Connection, booking: sqlite3.Row) -> None:
    if booking["vehicle_id"] is not None:
        conn.execute(
            f"UPDATE vehicle SET status = 'IN_STOCK', updated_at = {NOW} WHERE id = ? AND status = 'BOOKED'",
            (booking["vehicle_id"],),
        )


def cancel_booking(conn: sqlite3.Connection, booking_id: int, reason: str) -> None:
    """Cancel a booking and free its vehicle. Any advance held is then refunded with refund_advance."""
    if not reason.strip():
        raise BusinessError("Give a reason for cancelling the booking.")
    with transaction(conn):
        booking = _booking(conn, booking_id)
        _require_open(booking)
        _release_vehicle(conn, booking)
        conn.execute(
            f"UPDATE booking SET status = 'CANCELLED', cancel_reason = ?, updated_at = {NOW} WHERE id = ?",
            (reason.strip(), booking_id),
        )


def refund_advance(
    conn: sqlite3.Connection, booking_id: int, *, amount: int, mode: str, reference_no: str | None = None,
    on: date | None = None,
) -> str:
    if amount <= 0:
        raise BusinessError("Amount must be more than zero.")
    with transaction(conn):
        booking = _booking(conn, booking_id)
        if booking["status"] != "CANCELLED":
            raise BusinessError("Cancel the booking before refunding its advance.")
        held = advance_held(conn, booking_id)
        if amount > held:
            raise BusinessError(f"Refund is more than the advance held ({format_inr(held)}).")
        return _record(conn, booking, amount=-amount, mode=mode, payment_type="REFUND",
                       reference_no=reference_no, on=on or date.today())


def find_bookings(
    conn: sqlite3.Connection, *, status: str | None = None, customer_id: int | None = None, text: str = "",
) -> list[sqlite3.Row]:
    where, args = [], []
    if status == "ACTIVE":
        where.append("b.status IN ('OPEN', 'ALLOTTED')")
    elif status:
        where.append("b.status = ?")
        args.append(status)
    if customer_id is not None:
        where.append("b.customer_id = ?")
        args.append(customer_id)
    if text.strip():
        like = f"%{text.strip()}%"
        where.append("(b.booking_no LIKE ? OR c.name LIKE ? OR c.phone LIKE ? OR v.chassis_number LIKE ?)")
        args += [like] * 4
    return conn.execute(
        f"""
        SELECT b.*, c.name AS customer_name, c.phone AS customer_phone, m.model_name, m.variant,
               v.chassis_number, v.color AS vehicle_color, i.invoice_no,
               (SELECT coalesce(sum(amount), 0) FROM payment p
                 WHERE p.booking_id = b.id AND p.invoice_id IS NULL) AS advance_held,
               (SELECT coalesce(sum(amount), 0) FROM payment p
                 WHERE p.booking_id = b.id AND p.payment_type = 'BOOKING_ADVANCE') AS advance_paid
        FROM booking b
        JOIN customer c ON c.id = b.customer_id
        JOIN vehicle_model m ON m.id = b.vehicle_model_id
        LEFT JOIN vehicle v ON v.id = b.vehicle_id
        LEFT JOIN invoice i ON i.id = b.invoice_id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY CASE WHEN b.status IN ('OPEN', 'ALLOTTED') THEN 0 ELSE 1 END, b.booking_date DESC, b.id DESC
        """,
        args,
    ).fetchall()
