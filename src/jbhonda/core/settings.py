import sqlite3

from jbhonda.core.errors import BusinessError
from jbhonda.core.validation import is_valid_gstin, is_valid_pincode
from jbhonda.db import NOW, transaction

FIELDS = (
    "shop_name", "address_line1", "address_line2", "city", "district", "state", "pincode", "state_code",
    "gstin", "pan", "phone", "email", "bank_name", "bank_account_no", "bank_ifsc", "invoice_terms", "logo_path",
)


def get_settings(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM shop_settings WHERE id = 1").fetchone()


def update_settings(conn: sqlite3.Connection, **values: str | None) -> None:
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise TypeError(f"unknown settings: {sorted(unknown)}")
    values = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in values.items()}

    if "shop_name" in values and not values["shop_name"]:
        raise BusinessError("Shop name is required.")
    for key in ("gstin", "pan", "bank_ifsc"):
        if values.get(key):
            values[key] = values[key].upper()
    if values.get("gstin"):
        if not is_valid_gstin(values["gstin"]):
            raise BusinessError(f"GSTIN {values['gstin']} is not valid. Check it for typing mistakes.")
        values["state_code"] = values["gstin"][:2]  # the GSTIN fixes the state code
        if not values.get("pan"):
            values["pan"] = values["gstin"][2:12]
    elif "gstin" in values:
        values["state_code"] = None
    if values.get("pincode") and not is_valid_pincode(values["pincode"]):
        raise BusinessError("PIN code must be 6 digits.")

    if not values:
        return
    assignments = ", ".join(f"{k} = ?" for k in values)
    with transaction(conn):
        conn.execute(f"UPDATE shop_settings SET {assignments}, updated_at = {NOW} WHERE id = 1", tuple(values.values()))
