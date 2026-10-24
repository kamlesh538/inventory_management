"""Financers and insurers, plus activating/deactivating any master record.

Masters are never deleted, because old loans, policies and invoices refer to them;
deactivated ones just stop appearing in pick lists.
"""

import sqlite3

from jbhonda.core.errors import BusinessError
from jbhonda.db import NOW, transaction

_ACTIVATABLE = {"vehicle_model", "financer", "insurer", "part", "labour_item"}


def set_active(conn: sqlite3.Connection, table: str, record_id: int, active: bool) -> None:
    if table not in _ACTIVATABLE:
        raise ValueError(f"{table} cannot be activated/deactivated")
    with transaction(conn):
        updated = conn.execute(
            f"UPDATE {table} SET is_active = ?, updated_at = {NOW} WHERE id = ?", (int(active), record_id)
        ).rowcount
        if not updated:
            raise BusinessError("Record not found.")


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


# ---------------------------------------------------------------- financers

def save_financer(
    conn: sqlite3.Connection, *, name: str, branch: str = "", contact_person: str | None = None,
    phone: str | None = None, financer_id: int | None = None,
) -> int:
    """Add a financer, or update one when financer_id is given."""
    name, branch = name.strip(), branch.strip()
    if not name:
        raise BusinessError("Financer name is required.")
    with transaction(conn):
        clash = conn.execute(
            "SELECT id FROM financer WHERE name = ? COLLATE NOCASE AND branch = ? COLLATE NOCASE AND id IS NOT ?",
            (name, branch, financer_id),
        ).fetchone()
        if clash:
            raise BusinessError(f"{name} {branch}".strip() + " is already in the list.")
        values = (name, branch, _clean(contact_person), _clean(phone))
        if financer_id is None:
            return conn.execute(
                "INSERT INTO financer (name, branch, contact_person, phone) VALUES (?, ?, ?, ?)", values
            ).lastrowid
        conn.execute(
            f"UPDATE financer SET name = ?, branch = ?, contact_person = ?, phone = ?, updated_at = {NOW} WHERE id = ?",
            (*values, financer_id),
        )
        return financer_id


# ---------------------------------------------------------------- insurers

def save_insurer(
    conn: sqlite3.Connection, *, name: str, contact_person: str | None = None, phone: str | None = None,
    insurer_id: int | None = None,
) -> int:
    """Add an insurer, or update one when insurer_id is given."""
    name = name.strip()
    if not name:
        raise BusinessError("Insurance company name is required.")
    with transaction(conn):
        clash = conn.execute(
            "SELECT id FROM insurer WHERE name = ? COLLATE NOCASE AND id IS NOT ?", (name, insurer_id)
        ).fetchone()
        if clash:
            raise BusinessError(f"{name} is already in the list.")
        values = (name, _clean(contact_person), _clean(phone))
        if insurer_id is None:
            return conn.execute(
                "INSERT INTO insurer (name, contact_person, phone) VALUES (?, ?, ?)", values
            ).lastrowid
        conn.execute(
            f"UPDATE insurer SET name = ?, contact_person = ?, phone = ?, updated_at = {NOW} WHERE id = ?",
            (*values, insurer_id),
        )
        return insurer_id


# ---------------------------------------------------------------- labour price list

def save_labour_item(
    conn: sqlite3.Connection, *, name: str, default_charge: int, sac_code: str = "998714", gst_rate_bp: int = 1800,
    labour_item_id: int | None = None,
) -> int:
    """Add a standard labour job (charge incl. GST), or update one when labour_item_id is given."""
    name, sac_code = name.strip(), sac_code.strip()
    if not name:
        raise BusinessError("Labour name is required.")
    if not sac_code:
        raise BusinessError("SAC code is required.")
    if default_charge < 0:
        raise BusinessError("Charge cannot be negative.")
    if not 0 <= gst_rate_bp <= 10000:
        raise BusinessError("GST rate must be between 0% and 100%.")
    with transaction(conn):
        if conn.execute("SELECT 1 FROM labour_item WHERE name = ? AND id IS NOT ?", (name, labour_item_id)).fetchone():
            raise BusinessError(f"{name} is already in the list.")
        values = (name, default_charge, sac_code, gst_rate_bp)
        if labour_item_id is None:
            return conn.execute(
                "INSERT INTO labour_item (name, default_charge, sac_code, gst_rate_bp) VALUES (?, ?, ?, ?)", values
            ).lastrowid
        conn.execute(
            f"UPDATE labour_item SET name = ?, default_charge = ?, sac_code = ?, gst_rate_bp = ?, updated_at = {NOW}"
            " WHERE id = ?", (*values, labour_item_id),
        )
        return labour_item_id
