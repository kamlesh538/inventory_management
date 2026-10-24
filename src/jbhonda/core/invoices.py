"""Reading invoices back: the list with what's been paid, and everything needed to print one."""

import sqlite3
from dataclasses import dataclass
from datetime import date

_PAID = "(SELECT coalesce(sum(p.amount), 0) FROM payment p WHERE p.invoice_id = i.id)"


def find_invoices(
    conn: sqlite3.Connection, *, text: str = "", invoice_type: str | None = None, status: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
) -> list[sqlite3.Row]:
    """status: 'ACTIVE', 'CANCELLED', 'DUE' (active with balance to collect) or 'REFUND' (money to return)."""
    where, args = [], []
    if text.strip():
        like = f"%{text.strip()}%"
        where.append(
            "(i.invoice_no LIKE ? OR i.bill_name LIKE ? OR i.bill_phone LIKE ? OR EXISTS ("
            " SELECT 1 FROM invoice_item ii JOIN vehicle v ON v.id = ii.vehicle_id"
            " WHERE ii.invoice_id = i.id AND (v.chassis_number LIKE ? OR v.engine_number LIKE ?"
            " OR v.registration_number LIKE ?)))"
        )
        args += [like] * 6
    if invoice_type:
        where.append("i.invoice_type = ?")
        args.append(invoice_type)
    if status in ("ACTIVE", "CANCELLED"):
        where.append("i.status = ?")
        args.append(status)
    elif status == "DUE":
        where.append(f"i.status = 'ACTIVE' AND i.grand_total > {_PAID}")
    elif status == "REFUND":
        where.append(f"(CASE WHEN i.status = 'ACTIVE' THEN i.grand_total ELSE 0 END) < {_PAID}")
    if date_from:
        where.append("i.invoice_date >= ?")
        args.append(date_from.isoformat())
    if date_to:
        where.append("i.invoice_date <= ?")
        args.append(date_to.isoformat())
    return conn.execute(
        f"""
        SELECT i.*, {_PAID} AS paid,
               (CASE WHEN i.status = 'ACTIVE' THEN i.grand_total ELSE 0 END) - {_PAID} AS balance,
               l.id AS loan_id, l.status AS loan_status, l.loan_amount
        FROM invoice i
        LEFT JOIN loan l ON l.invoice_id = i.id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY i.invoice_date DESC, i.id DESC
        """,
        args,
    ).fetchall()


@dataclass(frozen=True)
class HsnRow:
    hsn_code: str
    gst_rate_bp: int
    taxable: int
    cgst: int
    sgst: int


@dataclass(frozen=True)
class InvoiceDetail:
    invoice: sqlite3.Row
    items: list[sqlite3.Row]
    payments: list[sqlite3.Row]
    loan: sqlite3.Row | None
    insurance: sqlite3.Row | None
    vehicle: sqlite3.Row | None   # current record of the vehicle sold, for key/battery/mfg details
    booking_no: str | None
    hsn_summary: list[HsnRow]

    @property
    def paid(self) -> int:
        return sum(p["amount"] for p in self.payments)

    @property
    def balance(self) -> int:
        owed = self.invoice["grand_total"] if self.invoice["status"] == "ACTIVE" else 0
        return owed - self.paid


def invoice_detail(conn: sqlite3.Connection, invoice_id: int) -> InvoiceDetail:
    invoice = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
    if invoice is None:
        raise LookupError(f"invoice {invoice_id} not found")
    items = conn.execute("SELECT * FROM invoice_item WHERE invoice_id = ? ORDER BY line_no", (invoice_id,)).fetchall()
    payments = conn.execute(
        "SELECT * FROM payment WHERE invoice_id = ? ORDER BY payment_date, id", (invoice_id,)
    ).fetchall()
    loan = conn.execute(
        "SELECT l.*, f.name AS financer_name, f.branch AS financer_branch FROM loan l"
        " JOIN financer f ON f.id = l.financer_id WHERE l.invoice_id = ?", (invoice_id,)
    ).fetchone()
    insurance = conn.execute(
        "SELECT p.*, x.name AS insurer_name FROM insurance_policy p JOIN insurer x ON x.id = p.insurer_id"
        " WHERE p.invoice_id = ? ORDER BY p.id DESC", (invoice_id,)
    ).fetchone()
    vehicle_id = next((i["vehicle_id"] for i in items if i["vehicle_id"] is not None), None)
    vehicle = conn.execute("SELECT * FROM vehicle WHERE id = ?", (vehicle_id,)).fetchone() if vehicle_id else None
    booking = conn.execute(
        "SELECT booking_no FROM booking WHERE id = ?", (invoice["booking_id"],)
    ).fetchone() if invoice["booking_id"] else None

    groups: dict[tuple[str, int], list[int]] = {}
    for item in items:
        totals = groups.setdefault((item["hsn_code"], item["gst_rate_bp"]), [0, 0, 0])
        totals[0] += item["taxable_value"]
        totals[1] += item["cgst"]
        totals[2] += item["sgst"]
    hsn_summary = [HsnRow(hsn, rate, *t) for (hsn, rate), t in sorted(groups.items())]

    return InvoiceDetail(invoice, items, payments, loan, insurance, vehicle,
                         booking["booking_no"] if booking else None, hsn_summary)
