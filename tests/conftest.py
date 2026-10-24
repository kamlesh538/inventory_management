import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jbhonda.db import open_database


@pytest.fixture(autouse=True)
def no_blocking_message_boxes(monkeypatch):
    """A message box nobody expected would wait forever for a click; fail the test instead.

    Tests that expect a message patch jbhonda.ui.widgets helpers, which never reach these.
    """
    from PySide6.QtWidgets import QMessageBox

    def fail(*args, **kwargs):
        raise AssertionError(f"Unexpected message box: {args[1:3]}")

    for name in ("warning", "information", "question", "critical"):
        monkeypatch.setattr(QMessageBox, name, fail)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "data.db"


@pytest.fixture
def conn(db_path):
    conn = open_database(db_path)
    yield conn
    conn.close()


@pytest.fixture
def seeded(conn):
    """A database with one vehicle model, vehicle, customer and part in it."""
    conn.execute(
        "INSERT INTO vehicle_model (id, model_name, variant, category, engine_cc, hsn_code, gst_rate_bp, ex_showroom_price)"
        " VALUES (1, 'Activa 6G', 'DLX', 'SCOOTER', 110, '8711', 1800, 8500000)"
    )
    conn.execute(
        "INSERT INTO vehicle (id, vehicle_model_id, chassis_number, engine_number, color)"
        " VALUES (1, 1, 'ME4JF91AAPG000001', 'JF91EG0000001', 'Pearl White')"
    )
    conn.execute("INSERT INTO customer (id, name, phone) VALUES (1, 'Ramesh Kumar', '9876543210')")
    conn.execute(
        "INSERT INTO part (id, part_number, name, hsn_code, gst_rate_bp, sale_price)"
        " VALUES (1, '17210-K0N-D00', 'Air Filter', '8714', 1800, 35000)"
    )
    return conn


def add_vehicle_invoice(conn: sqlite3.Connection, invoice_id: int, vehicle_id: int = 1) -> None:
    conn.execute(
        "INSERT INTO invoice (id, invoice_no, invoice_type, invoice_date, customer_id, bill_name)"
        " VALUES (?, ?, 'VEHICLE', '2026-10-24', 1, 'Ramesh Kumar')",
        (invoice_id, f"JBH/V/2026-27/{invoice_id:04d}"),
    )
    conn.execute(
        "INSERT INTO invoice_item (invoice_id, line_no, item_kind, vehicle_id, description, hsn_code,"
        " quantity, unit_price, taxable_value, gst_rate_bp, cgst, sgst, line_total)"
        " VALUES (?, 1, 'VEHICLE', ?, 'Activa 6G DLX', '8711', 1, 8500000, 7203390, 1800, 648305, 648305, 8500000)",
        (invoice_id, vehicle_id),
    )
