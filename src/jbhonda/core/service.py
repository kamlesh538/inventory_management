"""Workshop servicing: job card -> parts issued and labour added while working -> bill at delivery.

Parts leave stock the moment they're issued to a job (JOB_ISSUE in the stock ledger) and go back
if removed or if the job is cancelled (JOB_RETURN). Billing turns the job into a SERVICE invoice
without touching stock again. Cancelling that invoice re-opens the job card.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from jbhonda.core.customers import billing_snapshot
from jbhonda.core.errors import BusinessError
from jbhonda.core.gst import round_to_rupee
from jbhonda.core.money import format_inr
from jbhonda.core.numbering import next_number
from jbhonda.core.sales import PaymentIn, _create_invoice, _Line, balance_due, record_payment
from jbhonda.db import NOW, transaction

SERVICE_TYPES = ("PAID", "FREE", "WARRANTY", "ACCIDENT")
DEFAULT_SAC = "998714"   # maintenance and repair services of motorcycles
DEFAULT_LABOUR_GST_BP = 1800

_HEADER_FIELDS = (
    "registration_number", "vehicle_description", "chassis_number", "odometer_km", "service_type",
    "free_service_no", "complaints", "work_done", "mechanic_name", "promised_at", "next_service_date",
    "next_service_km", "notes",
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _clean(value):
    if isinstance(value, str):
        return value.strip() or None
    return value


def _validate_header(fields: dict) -> None:
    if "vehicle_description" in fields and not fields["vehicle_description"]:
        raise BusinessError("Enter the vehicle model (e.g. Activa 6G).")
    service_type = fields.get("service_type")
    if service_type is not None and service_type not in SERVICE_TYPES:
        raise BusinessError("Choose the service type.")
    if service_type == "FREE" and not fields.get("free_service_no"):
        raise BusinessError("Choose which free service this is (1st, 2nd, ...).")
    if service_type is not None and service_type != "FREE":
        fields["free_service_no"] = None
    for key in ("odometer_km", "next_service_km"):
        if fields.get(key) is not None and fields[key] < 0:
            raise BusinessError("Kilometres cannot be negative.")


def _job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    job = conn.execute("SELECT * FROM service_job WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise BusinessError("Job card not found.")
    return job


def _require_open(job: sqlite3.Row) -> None:
    if job["status"] != "OPEN":
        raise BusinessError(f"Job card {job['job_no']} is {job['status'].lower()}; it can't be changed.")


# ---------------------------------------------------------------- job card

def create_job(
    conn: sqlite3.Connection, *, customer_id: int, vehicle_description: str = "", registration_number: str | None = None,
    vehicle_id: int | None = None, chassis_number: str | None = None, odometer_km: int | None = None,
    service_type: str = "PAID", free_service_no: int | None = None, complaints: str | None = None,
    mechanic_name: str | None = None, promised_at: str | None = None, on: datetime | None = None,
) -> int:
    """Open a job card when the vehicle arrives. Returns the job id.

    For a vehicle we sold, pass vehicle_id and its model/chassis/registration are filled in.
    """
    received = (on or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
    with transaction(conn):
        if conn.execute("SELECT 1 FROM customer WHERE id = ?", (customer_id,)).fetchone() is None:
            raise BusinessError("Choose the customer, or add a new one.")
        if vehicle_id is not None:
            v = conn.execute(
                "SELECT v.*, m.model_name, m.variant FROM vehicle v JOIN vehicle_model m ON m.id = v.vehicle_model_id"
                " WHERE v.id = ?", (vehicle_id,)).fetchone()
            if v is None:
                raise BusinessError("Vehicle not found.")
            vehicle_description = vehicle_description or f"{v['model_name']} {v['variant']}".strip()
            registration_number = registration_number or v["registration_number"]
            chassis_number = chassis_number or v["chassis_number"]
        fields = {
            "registration_number": (_clean(registration_number) or "").upper() or None,
            "vehicle_description": _clean(vehicle_description),
            "chassis_number": (_clean(chassis_number) or "").upper() or None,
            "odometer_km": odometer_km, "service_type": service_type, "free_service_no": free_service_no,
            "complaints": _clean(complaints), "mechanic_name": _clean(mechanic_name), "promised_at": _clean(promised_at),
        }
        _validate_header(fields)
        return conn.execute(
            "INSERT INTO service_job (job_no, customer_id, vehicle_id, registration_number, vehicle_description,"
            " chassis_number, odometer_km, service_type, free_service_no, complaints, mechanic_name, received_at,"
            " promised_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                next_number(conn, "JOB", (on or datetime.now()).date()), customer_id, vehicle_id,
                fields["registration_number"], fields["vehicle_description"], fields["chassis_number"],
                odometer_km, service_type, fields["free_service_no"], fields["complaints"], fields["mechanic_name"],
                received, fields["promised_at"],
            ),
        ).lastrowid


def update_job(conn: sqlite3.Connection, job_id: int, **fields) -> None:
    """Change job card details (vehicle, km, complaints, mechanic...) while it's open."""
    unknown = set(fields) - set(_HEADER_FIELDS)
    if unknown:
        raise TypeError(f"unknown job fields: {sorted(unknown)}")
    fields = {k: _clean(v) for k, v in fields.items()}
    for key in ("registration_number", "chassis_number"):
        if fields.get(key):
            fields[key] = fields[key].upper()
    with transaction(conn):
        job = _job(conn, job_id)
        _require_open(job)
        merged = {**dict(job), **fields}
        _validate_header(merged)  # also clears free_service_no unless it's a free service
        fields["free_service_no"] = merged["free_service_no"]
        assignments = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE service_job SET {assignments}, updated_at = {NOW} WHERE id = ?",
                     (*fields.values(), job_id))


def add_job_part(
    conn: sqlite3.Connection, job_id: int, *, part_id: int, quantity: int, unit_price: int | None = None,
    discount: int = 0, on: date | None = None,
) -> int:
    """Issue a part from stock to the job. Returns the job-part line id."""
    if quantity <= 0:
        raise BusinessError("Quantity must be more than zero.")
    with transaction(conn):
        job = _job(conn, job_id)
        _require_open(job)
        part = conn.execute("SELECT * FROM part WHERE id = ?", (part_id,)).fetchone()
        if part is None or not part["is_active"]:
            raise BusinessError("Choose an active part.")
        if part["stock_qty"] < quantity:
            raise BusinessError(f"Only {part['stock_qty']} of {part['part_number']} ({part['name']}) in stock.")
        price = part["sale_price"] if unit_price is None else unit_price
        if price < 0:
            raise BusinessError("Price cannot be negative.")
        if part["mrp"] and price > part["mrp"]:
            raise BusinessError(f"Price of {part['part_number']} cannot be more than its MRP.")
        if not 0 <= discount <= price * quantity:
            raise BusinessError("Discount cannot be negative or more than the line amount.")
        line_id = conn.execute(
            "INSERT INTO service_job_part (job_id, part_id, quantity, unit_price, discount, unit_cost)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, part_id, quantity, price, discount, part["avg_cost"]),
        ).lastrowid
        conn.execute(
            "INSERT INTO part_stock_movement (part_id, movement_type, quantity, unit_cost, reference_no,"
            " service_job_id, movement_date) VALUES (?, 'JOB_ISSUE', ?, ?, ?, ?, ?)",
            (part_id, -quantity, part["avg_cost"], job["job_no"], job_id, (on or date.today()).isoformat()),
        )
        return line_id


def remove_job_part(conn: sqlite3.Connection, job_part_id: int, on: date | None = None) -> None:
    """Take a part off the job; it goes back into stock."""
    with transaction(conn):
        line = conn.execute("SELECT * FROM service_job_part WHERE id = ?", (job_part_id,)).fetchone()
        if line is None:
            raise BusinessError("That part is not on the job card.")
        job = _job(conn, line["job_id"])
        _require_open(job)
        _return_part(conn, job, line, on or date.today(), "removed from job")
        conn.execute("DELETE FROM service_job_part WHERE id = ?", (job_part_id,))


def _return_part(conn: sqlite3.Connection, job: sqlite3.Row, line: sqlite3.Row, on: date, note: str) -> None:
    conn.execute(
        "INSERT INTO part_stock_movement (part_id, movement_type, quantity, unit_cost, reference_no, service_job_id,"
        " movement_date, notes) VALUES (?, 'JOB_RETURN', ?, ?, ?, ?, ?, ?)",
        (line["part_id"], line["quantity"], line["unit_cost"], job["job_no"], job["id"], on.isoformat(), note),
    )


def add_job_labour(
    conn: sqlite3.Connection, job_id: int, *, description: str = "", amount: int | None = None,
    labour_item_id: int | None = None,
) -> int:
    """Add a labour charge (incl. GST). From the labour price list, or a custom description."""
    with transaction(conn):
        _require_open(_job(conn, job_id))
        sac, rate = DEFAULT_SAC, DEFAULT_LABOUR_GST_BP
        if labour_item_id is not None:
            item = conn.execute("SELECT * FROM labour_item WHERE id = ?", (labour_item_id,)).fetchone()
            if item is None:
                raise BusinessError("Labour item not found.")
            description = description.strip() or item["name"]
            amount = item["default_charge"] if amount is None else amount
            sac, rate = item["sac_code"], item["gst_rate_bp"]
        description = description.strip()
        if not description:
            raise BusinessError("Describe the labour (e.g. General service).")
        if amount is None or amount < 0:
            raise BusinessError("Enter the labour charge (0 for free).")
        return conn.execute(
            "INSERT INTO service_job_labour (job_id, labour_item_id, description, amount, sac_code, gst_rate_bp)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, labour_item_id, description, amount, sac, rate),
        ).lastrowid


def remove_job_labour(conn: sqlite3.Connection, labour_id: int) -> None:
    with transaction(conn):
        line = conn.execute("SELECT * FROM service_job_labour WHERE id = ?", (labour_id,)).fetchone()
        if line is None:
            raise BusinessError("That labour line is not on the job card.")
        _require_open(_job(conn, line["job_id"]))
        conn.execute("DELETE FROM service_job_labour WHERE id = ?", (labour_id,))


@dataclass(frozen=True)
class JobTotals:
    parts: int
    labour: int
    total: int  # rounded to the rupee, as it will be billed


def job_lines(conn: sqlite3.Connection, job_id: int) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    parts = conn.execute(
        "SELECT jp.*, p.part_number, p.name, p.hsn_code, p.gst_rate_bp FROM service_job_part jp"
        " JOIN part p ON p.id = jp.part_id WHERE jp.job_id = ? ORDER BY jp.id", (job_id,)).fetchall()
    labour = conn.execute("SELECT * FROM service_job_labour WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
    return parts, labour


def job_totals(conn: sqlite3.Connection, job_id: int) -> JobTotals:
    parts, labour = job_lines(conn, job_id)
    parts_total = sum(p["quantity"] * p["unit_price"] - p["discount"] for p in parts)
    labour_total = sum(x["amount"] for x in labour)
    return JobTotals(parts_total, labour_total, round_to_rupee(parts_total + labour_total)[0])


# ---------------------------------------------------------------- finishing

def bill_job(
    conn: sqlite3.Connection, job_id: int, *, payments: Sequence[PaymentIn] = (), work_done: str | None = None,
    next_service_date: date | None = None, next_service_km: int | None = None, on: date | None = None,
) -> int:
    """Deliver the vehicle: make the SERVICE invoice from the job card and record payment. Returns invoice id."""
    on = on or date.today()
    with transaction(conn):
        job = _job(conn, job_id)
        _require_open(job)
        parts, labour = job_lines(conn, job_id)
        if not parts and not labour:
            raise BusinessError("Add the parts used and/or labour before billing.")
        customer = conn.execute("SELECT * FROM customer WHERE id = ?", (job["customer_id"],)).fetchone()
        lines = [
            _Line(item_kind="PART", vehicle_id=None, part_id=p["part_id"], description=f"{p['name']} ({p['part_number']})",
                  hsn_code=p["hsn_code"], quantity=p["quantity"], unit_price=p["unit_price"], discount=p["discount"],
                  gst_rate_bp=p["gst_rate_bp"], unit_cost=p["unit_cost"])
            for p in parts
        ] + [
            _Line(item_kind="LABOUR", vehicle_id=None, part_id=None, description=f"Labour: {x['description']}",
                  hsn_code=x["sac_code"], quantity=1, unit_price=x["amount"], discount=0,
                  gst_rate_bp=x["gst_rate_bp"], unit_cost=0)
            for x in labour
        ]
        free = f"Free service {job['free_service_no']}. " if job["service_type"] == "FREE" else ""
        invoice_id = _create_invoice(
            conn, "SERVICE", on, billing_snapshot(customer), lines, customer_id=job["customer_id"],
            notes=f"{free}Job card {job['job_no']}", take_parts_from_stock=False,
        )
        conn.execute(
            f"UPDATE service_job SET status = 'BILLED', invoice_id = ?, delivered_at = ?, work_done = coalesce(?, work_done),"
            f" next_service_date = ?, next_service_km = ?, updated_at = {NOW} WHERE id = ?",
            (invoice_id, _now(), _clean(work_done), next_service_date.isoformat() if next_service_date else None,
             next_service_km, job_id),
        )
        due = balance_due(conn, invoice_id)
        paying = sum(p.amount for p in payments)
        if paying > due:
            raise BusinessError(f"Payment ({format_inr(paying)}) is more than the bill ({format_inr(due)}).")
        for p in payments:
            if p.mode == "FINANCE":
                raise BusinessError("Finance is only for vehicle sales.")
            record_payment(conn, invoice_id=invoice_id, amount=p.amount, mode=p.mode,
                           payment_type="FULL_PAYMENT" if paying == due else "DOWN_PAYMENT",
                           reference_no=p.reference_no, on=on)
        return invoice_id


def cancel_job(conn: sqlite3.Connection, job_id: int, reason: str, on: date | None = None) -> None:
    """Cancel an open job card; all its parts go back into stock."""
    if not reason.strip():
        raise BusinessError("Give a reason for cancelling the job card.")
    with transaction(conn):
        job = _job(conn, job_id)
        _require_open(job)
        parts, _ = job_lines(conn, job_id)
        for line in parts:
            _return_part(conn, job, line, on or date.today(), f"job cancelled: {reason.strip()}")
        conn.execute(
            f"UPDATE service_job SET status = 'CANCELLED', cancel_reason = ?, updated_at = {NOW} WHERE id = ?",
            (reason.strip(), job_id),
        )


# ---------------------------------------------------------------- lookups

def find_jobs(conn: sqlite3.Connection, *, status: str | None = None, text: str = "") -> list[sqlite3.Row]:
    where, args = [], []
    if status:
        where.append("j.status = ?")
        args.append(status)
    if text.strip():
        like = f"%{text.strip()}%"
        where.append("(j.job_no LIKE ? OR j.registration_number LIKE ? OR c.name LIKE ? OR c.phone LIKE ?"
                     " OR j.vehicle_description LIKE ? OR j.chassis_number LIKE ?)")
        args += [like] * 6
    return conn.execute(
        f"""
        SELECT j.*, c.name AS customer_name, c.phone AS customer_phone, i.invoice_no, i.grand_total,
               (SELECT coalesce(sum(quantity * unit_price - discount), 0) FROM service_job_part WHERE job_id = j.id)
             + (SELECT coalesce(sum(amount), 0) FROM service_job_labour WHERE job_id = j.id) AS running_total
        FROM service_job j
        JOIN customer c ON c.id = j.customer_id
        LEFT JOIN invoice i ON i.id = j.invoice_id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY CASE j.status WHEN 'OPEN' THEN 0 ELSE 1 END, j.received_at DESC, j.id DESC
        """,
        args,
    ).fetchall()


def customer_vehicles(conn: sqlite3.Connection, customer_id: int) -> list[dict]:
    """Vehicles this customer is known to have: ones we sold them, and ones serviced before."""
    found: dict[str, dict] = {}
    for v in conn.execute(
        "SELECT v.*, m.model_name, m.variant FROM vehicle v JOIN vehicle_model m ON m.id = v.vehicle_model_id"
        " JOIN invoice_item ii ON ii.vehicle_id = v.id JOIN invoice i ON i.id = ii.invoice_id"
        " WHERE i.customer_id = ? AND i.status = 'ACTIVE'", (customer_id,)
    ):
        found[v["chassis_number"]] = {
            "vehicle_id": v["id"], "vehicle_description": f"{v['model_name']} {v['variant']}".strip(),
            "registration_number": v["registration_number"], "chassis_number": v["chassis_number"],
        }
    for j in conn.execute(
        "SELECT * FROM service_job WHERE customer_id = ? ORDER BY received_at DESC", (customer_id,)
    ):
        key = j["chassis_number"] or j["registration_number"] or j["vehicle_description"]
        found.setdefault(key, {
            "vehicle_id": j["vehicle_id"], "vehicle_description": j["vehicle_description"],
            "registration_number": j["registration_number"], "chassis_number": j["chassis_number"],
        })
    return list(found.values())
