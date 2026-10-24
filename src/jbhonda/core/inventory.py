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


def add_vehicle(
    conn: sqlite3.Connection, *, vehicle_model_id: int, chassis_number: str, engine_number: str, color: str,
    key_number: str | None = None, battery_number: str | None = None, manufacturing_month: str | None = None,
    purchase_invoice_no: str | None = None, purchase_date: date | None = None,
    purchase_taxable_value: int = 0, purchase_gst_amount: int = 0, notes: str | None = None,
) -> int:
    chassis_number = chassis_number.strip().upper()
    engine_number = engine_number.strip().upper()
    if not chassis_number or not engine_number:
        raise BusinessError("Chassis number and engine number are required.")
    if not color.strip():
        raise BusinessError("Colour is required.")
    _require_non_negative(purchase_taxable_value, "Purchase price")
    _require_non_negative(purchase_gst_amount, "Purchase GST")
    with transaction(conn):
        model = conn.execute("SELECT is_active FROM vehicle_model WHERE id = ?", (vehicle_model_id,)).fetchone()
        if model is None:
            raise BusinessError("Choose a vehicle model.")
        if not model["is_active"]:
            raise BusinessError("That vehicle model is no longer active.")
        for column, value, label in (
            ("chassis_number", chassis_number, "chassis"),
            ("engine_number", engine_number, "engine"),
        ):
            if conn.execute(f"SELECT 1 FROM vehicle WHERE {column} = ?", (value,)).fetchone():
                raise BusinessError(f"A vehicle with {label} number {value} is already in the system.")
        return conn.execute(
            "INSERT INTO vehicle (vehicle_model_id, chassis_number, engine_number, color, key_number, battery_number,"
            " manufacturing_month, purchase_invoice_no, purchase_date, purchase_taxable_value, purchase_gst_amount, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                vehicle_model_id, chassis_number, engine_number, color.strip(), key_number, battery_number,
                manufacturing_month, purchase_invoice_no, purchase_date.isoformat() if purchase_date else None,
                purchase_taxable_value, purchase_gst_amount, notes,
            ),
        ).lastrowid


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
