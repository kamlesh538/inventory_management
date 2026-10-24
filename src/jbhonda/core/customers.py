import sqlite3

from jbhonda.core.errors import BusinessError
from jbhonda.core.validation import is_valid_gstin, is_valid_pincode, normalize_mobile
from jbhonda.db import NOW, transaction

OPTIONAL_FIELDS = (
    "relation_type", "relation_name", "alt_phone", "email",
    "address_line1", "address_line2", "city", "district", "state", "pincode",
    "gstin", "id_proof_type", "id_proof_number", "date_of_birth",
)
RELATION_TYPES = ("S/O", "D/O", "W/O", "C/O")
ID_PROOF_TYPES = ("AADHAAR", "PAN", "DL", "VOTER_ID", "PASSPORT")


def save_customer(
    conn: sqlite3.Connection, *, name: str, phone: str, customer_id: int | None = None, **details: str | None,
) -> int:
    """Add a customer, or update one when customer_id is given (fields not passed are left unchanged).

    Old invoices keep the customer details they were billed with.
    """
    unknown = set(details) - set(OPTIONAL_FIELDS)
    if unknown:
        raise TypeError(f"unknown customer fields: {sorted(unknown)}")
    name = " ".join(name.split())
    if not name:
        raise BusinessError("Customer name is required.")
    phone = normalize_mobile(phone)
    if phone is None:
        raise BusinessError("Enter a valid 10-digit mobile number.")
    details = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in details.items()}
    if details.get("alt_phone"):
        details["alt_phone"] = normalize_mobile(details["alt_phone"]) or details["alt_phone"]
    if details.get("pincode") and not is_valid_pincode(details["pincode"]):
        raise BusinessError("PIN code must be 6 digits.")
    if details.get("gstin"):
        details["gstin"] = details["gstin"].upper()
        if not is_valid_gstin(details["gstin"]):
            raise BusinessError("Customer GSTIN is not valid.")
    if details.get("relation_type") not in (None, *RELATION_TYPES):
        raise BusinessError("Choose S/O, D/O, W/O or C/O.")
    if details.get("id_proof_type") not in (None, *ID_PROOF_TYPES):
        raise BusinessError("Choose a valid ID proof type.")
    if details.get("id_proof_number"):
        details["id_proof_number"] = details["id_proof_number"].upper()

    fields = {"name": name, "phone": phone, **details}
    with transaction(conn):
        if customer_id is None:
            columns = ", ".join(fields)
            marks = ", ".join("?" for _ in fields)
            return conn.execute(f"INSERT INTO customer ({columns}) VALUES ({marks})", tuple(fields.values())).lastrowid
        assignments = ", ".join(f"{k} = ?" for k in fields)
        updated = conn.execute(
            f"UPDATE customer SET {assignments}, updated_at = {NOW} WHERE id = ?", (*fields.values(), customer_id)
        ).rowcount
        if not updated:
            raise BusinessError("Customer not found.")
        return customer_id


def search_customers(conn: sqlite3.Connection, text: str, limit: int = 50) -> list[sqlite3.Row]:
    """Match by phone digits or by name; most recently added first."""
    text = text.strip()
    if not text:
        return conn.execute("SELECT * FROM customer ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    like = f"%{text}%"
    return conn.execute(
        "SELECT * FROM customer WHERE phone LIKE ? OR alt_phone LIKE ? OR name LIKE ? ORDER BY id DESC LIMIT ?",
        (like, like, like, limit),
    ).fetchall()


def customer_label(customer: sqlite3.Row) -> str:
    return f"{customer['name']}  ({customer['phone']})"


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
