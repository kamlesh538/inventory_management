"""Deleting records entered by mistake (or for testing).

Anything that's been used - a vehicle that was sold, a part that was billed, a customer with an
invoice - can't be deleted, because invoices and history refer to it. The database's foreign keys
enforce that; this module turns their refusal into a message staff can act on.
Invoices are never deleted (GST needs an unbroken number series); cancel them instead.
"""

import sqlite3

from jbhonda.core.errors import BusinessError
from jbhonda.db import transaction

_IN_USE = {
    "vehicle": "This vehicle has been booked, sold or serviced, so it can't be deleted.",
    "vehicle_model": "Vehicles or bookings use this model, so it can't be deleted. Deactivate it instead.",
    "customer": "This customer has invoices, bookings, payments or job cards, so they can't be deleted.",
    "financer": "Loans refer to this financer, so it can't be deleted. Deactivate it instead.",
    "insurer": "Policies refer to this insurance company, so it can't be deleted. Deactivate it instead.",
    "labour_item": "Job cards use this labour item, so it can't be deleted. Deactivate it instead.",
}


def delete_record(conn: sqlite3.Connection, table: str, record_id: int) -> None:
    if table == "part":
        return delete_part(conn, record_id)
    if table not in _IN_USE:
        raise ValueError(f"{table} records cannot be deleted")
    try:
        with transaction(conn):
            if conn.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,)).rowcount == 0:
                raise BusinessError("Record not found.")
    except sqlite3.IntegrityError as exc:
        if "FOREIGN KEY" not in str(exc):
            raise
        raise BusinessError(_IN_USE[table]) from None


def delete_part(conn: sqlite3.Connection, part_id: int) -> None:
    """Delete a part that was never sold or used on a job, along with its stock-in history."""
    with transaction(conn):
        used = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM invoice_item WHERE part_id = ?)"
            " OR EXISTS (SELECT 1 FROM service_job_part WHERE part_id = ?)",
            (part_id, part_id),
        ).fetchone()[0]
        if used:
            raise BusinessError("This part has been sold or used on a job card, so it can't be deleted. "
                                "Deactivate it instead.")
        conn.execute("DELETE FROM part_stock_movement WHERE part_id = ?", (part_id,))
        if conn.execute("DELETE FROM part WHERE id = ?", (part_id,)).rowcount == 0:
            raise BusinessError("Part not found.")
