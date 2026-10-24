import sqlite3

from jbhonda.core.errors import BusinessError
from jbhonda.db import transaction

_OPTIONAL_FIELDS = {
    "relation_type", "relation_name", "alt_phone", "email",
    "address_line1", "address_line2", "city", "district", "state", "pincode",
    "gstin", "id_proof_type", "id_proof_number", "date_of_birth",
}


def add_customer(conn: sqlite3.Connection, *, name: str, phone: str, **details: str | None) -> int:
    unknown = set(details) - _OPTIONAL_FIELDS
    if unknown:
        raise TypeError(f"unknown customer fields: {sorted(unknown)}")
    name, phone = name.strip(), phone.strip()
    if not name:
        raise BusinessError("Customer name is required.")
    if not phone:
        raise BusinessError("Customer phone number is required.")
    fields = {"name": name, "phone": phone, **{k: v for k, v in details.items() if v not in (None, "")}}
    columns = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with transaction(conn):
        return conn.execute(f"INSERT INTO customer ({columns}) VALUES ({marks})", tuple(fields.values())).lastrowid


def find_by_phone(conn: sqlite3.Connection, phone: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM customer WHERE phone = ? OR alt_phone = ? ORDER BY id DESC", (phone.strip(), phone.strip())
    ).fetchall()


def billing_snapshot(customer: sqlite3.Row) -> dict[str, str | None]:
    """The customer details copied onto an invoice."""
    address = ", ".join(
        part for part in (
            customer["address_line1"], customer["address_line2"], customer["city"],
            customer["district"], customer["state"], customer["pincode"],
        ) if part
    )
    relation = None
    if customer["relation_type"] and customer["relation_name"]:
        relation = f"{customer['relation_type']} {customer['relation_name']}"
    return {
        "bill_name": customer["name"],
        "bill_relation": relation,
        "bill_phone": customer["phone"],
        "bill_address": address or None,
        "bill_gstin": customer["gstin"],
    }
