"""Rules the database enforces on its own, so no screen or bug can break them."""

import sqlite3

import pytest

from conftest import add_vehicle_invoice


def test_chassis_and_engine_numbers_unique_ignoring_case(seeded):
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO vehicle (vehicle_model_id, chassis_number, engine_number, color)"
            " VALUES (1, 'me4jf91aapg000001', 'OTHER', 'Red')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO vehicle (vehicle_model_id, chassis_number, engine_number, color)"
            " VALUES (1, 'OTHER', 'jf91eg0000001', 'Red')"
        )


def test_vehicle_cannot_be_sold_twice(seeded):
    add_vehicle_invoice(seeded, 1)
    with pytest.raises(sqlite3.IntegrityError, match="already on an active invoice"):
        add_vehicle_invoice(seeded, 2)


def test_vehicle_can_be_resold_after_cancellation(seeded):
    add_vehicle_invoice(seeded, 1)
    seeded.execute(
        "UPDATE invoice SET status = 'CANCELLED', cancelled_at = '2026-10-24T10:00:00',"
        " cancel_reason = 'wrong customer' WHERE id = 1"
    )
    add_vehicle_invoice(seeded, 2)


def test_cancelled_invoice_cannot_be_reactivated(seeded):
    add_vehicle_invoice(seeded, 1)
    seeded.execute("UPDATE invoice SET status = 'CANCELLED', cancelled_at = '2026-10-24T10:00:00' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute("UPDATE invoice SET status = 'ACTIVE', cancelled_at = NULL WHERE id = 1")


def test_invoices_and_lines_cannot_be_deleted(seeded):
    add_vehicle_invoice(seeded, 1)
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute("DELETE FROM invoice_item")
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute("DELETE FROM invoice")


def test_vehicle_invoice_needs_a_customer(seeded):
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO invoice (invoice_no, invoice_type, invoice_date, bill_name)"
            " VALUES ('V1', 'VEHICLE', '2026-10-24', 'Walk-in')"
        )
    seeded.execute(  # walk-in counter sale of parts is fine
        "INSERT INTO invoice (invoice_no, invoice_type, invoice_date, bill_name)"
        " VALUES ('P1', 'PART', '2026-10-24', 'Walk-in')"
    )


def _move(conn, qty, kind="PURCHASE", item=None):
    conn.execute(
        "INSERT INTO part_stock_movement (part_id, movement_type, quantity, unit_cost, invoice_item_id, movement_date)"
        " VALUES (1, ?, ?, 25000, ?, '2026-10-24')",
        (kind, qty, item),
    )


def test_stock_qty_follows_the_ledger(seeded):
    _move(seeded, 10)
    _move(seeded, -3, "ADJUSTMENT")
    stock = seeded.execute("SELECT stock_qty FROM part WHERE id = 1").fetchone()[0]
    ledger = seeded.execute("SELECT sum(quantity) FROM part_stock_movement WHERE part_id = 1").fetchone()[0]
    assert stock == ledger == 7


def test_cannot_sell_more_than_in_stock(seeded):
    _move(seeded, 2)
    with pytest.raises(sqlite3.IntegrityError):
        _move(seeded, -3, "ADJUSTMENT")
    assert seeded.execute("SELECT stock_qty FROM part WHERE id = 1").fetchone()[0] == 2


def test_movement_sign_must_match_type(seeded):
    with pytest.raises(sqlite3.IntegrityError):
        _move(seeded, -5, "PURCHASE")


def test_ledger_is_append_only(seeded):
    _move(seeded, 5)
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute("UPDATE part_stock_movement SET quantity = 50")
    seeded.execute(
        "INSERT INTO invoice (id, invoice_no, invoice_type, invoice_date, bill_name)"
        " VALUES (9, 'P9', 'PART', '2026-10-24', 'Walk-in')"
    )
    seeded.execute(
        "INSERT INTO invoice_item (invoice_id, line_no, item_kind, part_id, description, hsn_code, quantity,"
        " unit_price, taxable_value, gst_rate_bp, cgst, sgst, line_total) VALUES (9, 1, 'PART', 1, 'x', '8714', 1,"
        " 100, 84, 1800, 8, 8, 100)"
    )
    with pytest.raises(sqlite3.IntegrityError):  # the part has been sold: its history is permanent
        seeded.execute("DELETE FROM part_stock_movement")


def test_unsold_part_ledger_can_be_removed_with_the_part(seeded):
    _move(seeded, 5)
    seeded.execute("DELETE FROM part_stock_movement WHERE part_id = 1")
    seeded.execute("DELETE FROM part WHERE id = 1")


def test_payment_needs_invoice_or_booking(seeded):
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO payment (receipt_no, customer_id, payment_date, payment_type, payment_mode, amount)"
            " VALUES ('R1', 1, '2026-10-24', 'FULL_PAYMENT', 'CASH', 100)"
        )


def test_refund_must_be_negative(seeded):
    add_vehicle_invoice(seeded, 1)
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO payment (receipt_no, invoice_id, payment_date, payment_type, payment_mode, amount)"
            " VALUES ('R1', 1, '2026-10-24', 'REFUND', 'CASH', 100)"
        )


def test_money_columns_reject_decimals(seeded):
    # STRICT tables: a stray float like 850.5 can't sneak into a paise column.
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute("UPDATE part SET sale_price = 350.5 WHERE id = 1")
