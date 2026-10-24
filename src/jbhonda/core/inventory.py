import re
import sqlite3
from datetime import date

from jbhonda.core.errors import BusinessError
from jbhonda.core.money import div_round
from jbhonda.db import NOW, transaction


def _require_positive(value: int, label: str) -> None:
    if value <= 0:
        raise BusinessError(f"{label} must be more than zero.")


def _require_non_negative(value: int, label: str) -> None:
    if value < 0:
        raise BusinessError(f"{label} cannot be negative.")


def _check_gst_rate(gst_rate_bp: int) -> None:
    if not 0 <= gst_rate_bp <= 10000:
        raise BusinessError("GST rate must be between 0% and 100%.")


# ---------------------------------------------------------------- vehicles

VEHICLE_CATEGORIES = ("SCOOTER", "MOTORCYCLE", "EV")


def save_vehicle_model(
    conn: sqlite3.Connection, *, model_name: str, category: str, gst_rate_bp: int,
    ex_showroom_price: int, variant: str = "", engine_cc: int | None = None, hsn_code: str = "8711",
    model_id: int | None = None,
) -> int:
    """Add a vehicle model, or update one when model_id is given.

    Changing the price or GST rate only affects future sales; old invoices keep their own copy.
    """
    model_name, variant, hsn_code = model_name.strip(), variant.strip(), hsn_code.strip()
    if not model_name:
        raise BusinessError("Model name is required.")
    if category not in VEHICLE_CATEGORIES:
        raise BusinessError("Choose a category.")
    if not hsn_code:
        raise BusinessError("HSN code is required.")
    _check_gst_rate(gst_rate_bp)
    _require_non_negative(ex_showroom_price, "Ex-showroom price")
    if engine_cc is not None:
        _require_positive(engine_cc, "Engine CC")
    with transaction(conn):
        if conn.execute(
            "SELECT 1 FROM vehicle_model WHERE model_name = ? COLLATE NOCASE AND variant = ? COLLATE NOCASE"
            " AND id IS NOT ?",
            (model_name, variant, model_id),
        ).fetchone():
            raise BusinessError(f"Model '{f'{model_name} {variant}'.strip()}' already exists.")
        values = (model_name, variant, category, engine_cc, hsn_code, gst_rate_bp, ex_showroom_price)
        if model_id is None:
            return conn.execute(
                "INSERT INTO vehicle_model (model_name, variant, category, engine_cc, hsn_code, gst_rate_bp,"
                " ex_showroom_price) VALUES (?, ?, ?, ?, ?, ?, ?)",
                values,
            ).lastrowid
        conn.execute(
            "UPDATE vehicle_model SET model_name = ?, variant = ?, category = ?, engine_cc = ?, hsn_code = ?,"
            f" gst_rate_bp = ?, ex_showroom_price = ?, updated_at = {NOW} WHERE id = ?",
            (*values, model_id),
        )
        return model_id


VEHICLE_STATUSES = ("IN_STOCK", "BOOKED", "SOLD", "RETURNED_TO_OEM")

# Fields that can still be changed once a vehicle is sold; the rest are printed on its invoice.
_EDITABLE_AFTER_SALE = {"registration_number", "notes"}

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _text(value: str | None, upper: bool = False) -> str | None:
    value = (value or "").strip()
    return (value.upper() if upper else value) or None


def save_vehicle(
    conn: sqlite3.Connection, *, vehicle_model_id: int, chassis_number: str, engine_number: str, color: str,
    key_number: str | None = None, battery_number: str | None = None, manufacturing_month: str | None = None,
    purchase_invoice_no: str | None = None, purchase_date: date | None = None,
    purchase_taxable_value: int = 0, purchase_gst_amount: int = 0, registration_number: str | None = None,
    notes: str | None = None, vehicle_id: int | None = None,
) -> int:
    """Add a vehicle to stock, or update one when vehicle_id is given.

    Once sold, only the registration number and notes can change; cancel the invoice to fix the rest.
    """
    fields = {
        "vehicle_model_id": vehicle_model_id,
        "chassis_number": _text(chassis_number, upper=True),
        "engine_number": _text(engine_number, upper=True),
        "color": _text(color),
        "key_number": _text(key_number, upper=True),
        "battery_number": _text(battery_number, upper=True),
        "manufacturing_month": _text(manufacturing_month),
        "purchase_invoice_no": _text(purchase_invoice_no, upper=True),
        "purchase_date": purchase_date.isoformat() if purchase_date else None,
        "purchase_taxable_value": purchase_taxable_value,
        "purchase_gst_amount": purchase_gst_amount,
        "registration_number": _text(registration_number, upper=True),
        "notes": _text(notes),
    }
    if not fields["chassis_number"] or not fields["engine_number"]:
        raise BusinessError("Chassis number and engine number are required.")
    if not fields["color"]:
        raise BusinessError("Colour is required.")
    if fields["manufacturing_month"] and not _MONTH.match(fields["manufacturing_month"]):
        raise BusinessError("Manufacturing month must look like 2026-08.")
    if purchase_date and purchase_date > date.today():
        raise BusinessError("Purchase date cannot be in the future.")
    _require_non_negative(purchase_taxable_value, "Purchase price")
    _require_non_negative(purchase_gst_amount, "Purchase GST")

    with transaction(conn):
        existing = None
        if vehicle_id is not None:
            existing = conn.execute("SELECT * FROM vehicle WHERE id = ?", (vehicle_id,)).fetchone()
            if existing is None:
                raise BusinessError("Vehicle not found.")
            if existing["status"] == "SOLD":
                changed = {k for k, v in fields.items() if existing[k] != v} - _EDITABLE_AFTER_SALE
                if changed:
                    raise BusinessError(
                        "This vehicle is sold, so only the registration number and notes can be changed. "
                        "To correct other details, cancel its invoice first."
                    )

        model = conn.execute("SELECT is_active FROM vehicle_model WHERE id = ?", (vehicle_model_id,)).fetchone()
        if model is None:
            raise BusinessError("Choose a vehicle model.")
        model_changed = existing is None or existing["vehicle_model_id"] != vehicle_model_id
        if not model["is_active"] and model_changed:
            raise BusinessError("That vehicle model is no longer active.")
        for column, label in (("chassis_number", "chassis"), ("engine_number", "engine")):
            if conn.execute(
                f"SELECT 1 FROM vehicle WHERE {column} = ? AND id IS NOT ?", (fields[column], vehicle_id)
            ).fetchone():
                raise BusinessError(f"A vehicle with {label} number {fields[column]} is already in the system.")

        if existing is None:
            columns = ", ".join(fields)
            marks = ", ".join("?" for _ in fields)
            return conn.execute(f"INSERT INTO vehicle ({columns}) VALUES ({marks})", tuple(fields.values())).lastrowid
        assignments = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE vehicle SET {assignments}, updated_at = {NOW} WHERE id = ?", (*fields.values(), vehicle_id)
        )
        return vehicle_id


def find_vehicles(
    conn: sqlite3.Connection, *, text: str = "", vehicle_model_id: int | None = None, color: str | None = None,
    status: str | None = None,
) -> list[sqlite3.Row]:
    """Vehicles with their model and, if sold, the active invoice and buyer."""
    where, args = [], []
    if text.strip():
        like = f"%{text.strip()}%"
        where.append(
            "(v.chassis_number LIKE ? OR v.engine_number LIKE ? OR v.key_number LIKE ?"
            " OR v.registration_number LIKE ? OR m.model_name LIKE ? OR s.bill_name LIKE ?)"
        )
        args += [like] * 6
    if vehicle_model_id is not None:
        where.append("v.vehicle_model_id = ?")
        args.append(vehicle_model_id)
    if color:
        where.append("v.color = ? COLLATE NOCASE")
        args.append(color)
    if status:
        where.append("v.status = ?")
        args.append(status)
    return conn.execute(
        f"""
        SELECT v.*, m.model_name, m.variant, s.invoice_id, s.invoice_no, s.invoice_date, s.bill_name
        FROM vehicle v
        JOIN vehicle_model m ON m.id = v.vehicle_model_id
        LEFT JOIN (
            SELECT ii.vehicle_id, i.id AS invoice_id, i.invoice_no, i.invoice_date, i.bill_name
            FROM invoice_item ii JOIN invoice i ON i.id = ii.invoice_id
            WHERE i.status = 'ACTIVE' AND ii.vehicle_id IS NOT NULL
        ) s ON s.vehicle_id = v.id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY CASE v.status WHEN 'IN_STOCK' THEN 0 WHEN 'BOOKED' THEN 1 WHEN 'SOLD' THEN 2 ELSE 3 END,
                 coalesce(s.invoice_date, v.purchase_date) DESC, v.id DESC
        """,
        args,
    ).fetchall()


def vehicle_colors(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT color FROM vehicle ORDER BY color COLLATE NOCASE")]


# ---------------------------------------------------------------- parts

def add_part(
    conn: sqlite3.Connection, *, part_number: str, name: str, hsn_code: str, gst_rate_bp: int, sale_price: int,
    category: str = "SPARE", unit: str = "PCS", mrp: int = 0, reorder_level: int = 0,
    rack_location: str | None = None, opening_qty: int = 0, opening_unit_cost: int = 0,
) -> int:
    part_number, name = part_number.strip().upper(), name.strip()
    if not part_number or not name:
        raise BusinessError("Part number and name are required.")
    _check_gst_rate(gst_rate_bp)
    _require_non_negative(sale_price, "Sale price")
    _require_non_negative(mrp, "MRP")
    _require_non_negative(reorder_level, "Reorder level")
    _require_non_negative(opening_qty, "Opening stock")
    with transaction(conn):
        if conn.execute("SELECT 1 FROM part WHERE part_number = ?", (part_number,)).fetchone():
            raise BusinessError(f"Part number {part_number} is already in the system.")
        part_id = conn.execute(
            "INSERT INTO part (part_number, name, category, hsn_code, gst_rate_bp, unit, mrp, sale_price,"
            " reorder_level, rack_location) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (part_number, name, category, hsn_code.strip(), gst_rate_bp, unit, mrp, sale_price,
             reorder_level, rack_location),
        ).lastrowid
        if opening_qty:
            purchase_part(
                conn, part_id=part_id, quantity=opening_qty, unit_cost=opening_unit_cost,
                reference_no="OPENING STOCK",
            )
        return part_id


def purchase_part(
    conn: sqlite3.Connection, *, part_id: int, quantity: int, unit_cost: int, gst_amount: int = 0,
    reference_no: str | None = None, on: date | None = None, notes: str | None = None,
) -> None:
    """Stock in. unit_cost is per piece, excluding GST. Updates the weighted-average cost."""
    _require_positive(quantity, "Quantity")
    _require_non_negative(unit_cost, "Unit cost")
    _require_non_negative(gst_amount, "GST amount")
    with transaction(conn):
        part = conn.execute("SELECT stock_qty, avg_cost FROM part WHERE id = ?", (part_id,)).fetchone()
        if part is None:
            raise BusinessError("Part not found.")
        new_qty = part["stock_qty"] + quantity
        new_avg = div_round(part["stock_qty"] * part["avg_cost"] + quantity * unit_cost, new_qty)
        conn.execute(
            "INSERT INTO part_stock_movement (part_id, movement_type, quantity, unit_cost, gst_amount,"
            " reference_no, movement_date, notes) VALUES (?, 'PURCHASE', ?, ?, ?, ?, ?, ?)",
            (part_id, quantity, unit_cost, gst_amount, reference_no, (on or date.today()).isoformat(), notes),
        )
        conn.execute(f"UPDATE part SET avg_cost = ?, updated_at = {NOW} WHERE id = ?", (new_avg, part_id))


def adjust_stock(
    conn: sqlite3.Connection, *, part_id: int, quantity_change: int, reason: str, on: date | None = None,
) -> None:
    """Correct stock after a physical count, damage or loss. A reason is mandatory."""
    if quantity_change == 0:
        raise BusinessError("Adjustment quantity cannot be zero.")
    if not reason.strip():
        raise BusinessError("Give a reason for the stock adjustment.")
    with transaction(conn):
        part = conn.execute("SELECT part_number, stock_qty, avg_cost FROM part WHERE id = ?", (part_id,)).fetchone()
        if part is None:
            raise BusinessError("Part not found.")
        if part["stock_qty"] + quantity_change < 0:
            raise BusinessError(f"Only {part['stock_qty']} of {part['part_number']} in stock; cannot remove more.")
        conn.execute(
            "INSERT INTO part_stock_movement (part_id, movement_type, quantity, unit_cost, movement_date, notes)"
            " VALUES (?, 'ADJUSTMENT', ?, ?, ?, ?)",
            (part_id, quantity_change, part["avg_cost"], (on or date.today()).isoformat(), reason.strip()),
        )


def low_stock_parts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM part WHERE is_active = 1 AND stock_qty <= reorder_level ORDER BY name"
    ).fetchall()
