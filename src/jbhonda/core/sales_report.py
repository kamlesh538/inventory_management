"""Sales figures with filters: date range, vehicle/parts/service, loan or direct, payment mode.

Sales are counted by invoice date and exclude cancelled invoices. One invoice can be paid in
several modes (e.g. part cash, part UPI), so with a payment-mode filter the report shows the
amount received in those modes, not the whole invoice value; otherwise cash + UPI would add
up to more than was actually sold.
"""

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date

INVOICE_TYPES = ("VEHICLE", "PART", "SERVICE")
PAYMENT_MODES = ("CASH", "UPI", "CARD", "CHEQUE", "BANK_TRANSFER", "FINANCE")


@dataclass(frozen=True)
class Filters:
    date_from: date | None = None
    date_to: date | None = None
    types: Collection[str] = INVOICE_TYPES
    finance: str | None = None                     # None, "LOAN" or "DIRECT"
    modes: Collection[str] | None = None           # None = all modes, no mode filtering


@dataclass
class SalesReport:
    invoices: list[sqlite3.Row]
    count: int = 0
    sale_value: int = 0
    taxable: int = 0
    gst: int = 0
    received: int = 0            # all payments on these invoices
    received_in_modes: int = 0   # payments in the selected modes only
    outstanding: int = 0
    by_type: dict[str, dict[str, int]] = field(default_factory=dict)
    by_mode: dict[str, int] = field(default_factory=dict)


def sales_report(conn: sqlite3.Connection, f: Filters) -> SalesReport:
    types = [t for t in f.types if t in INVOICE_TYPES]
    if not types:
        return SalesReport(invoices=[])
    where = ["i.status = 'ACTIVE'", f"i.invoice_type IN ({', '.join('?' * len(types))})"]
    args: list = list(types)
    if f.date_from:
        where.append("i.invoice_date >= ?")
        args.append(f.date_from.isoformat())
    if f.date_to:
        where.append("i.invoice_date <= ?")
        args.append(f.date_to.isoformat())
    loan_exists = "EXISTS (SELECT 1 FROM loan l WHERE l.invoice_id = i.id AND l.status <> 'REJECTED')"
    if f.finance == "LOAN":
        where.append(loan_exists)
    elif f.finance == "DIRECT":
        where.append(f"NOT {loan_exists}")

    mode_list = [m for m in (f.modes if f.modes is not None else PAYMENT_MODES) if m in PAYMENT_MODES]
    mode_marks = ", ".join("?" * len(mode_list)) or "NULL"
    in_modes = f"(SELECT coalesce(sum(p.amount), 0) FROM payment p WHERE p.invoice_id = i.id" \
               f" AND p.payment_mode IN ({mode_marks}))"
    if f.modes is not None:
        where.append(f"EXISTS (SELECT 1 FROM payment p WHERE p.invoice_id = i.id AND p.payment_mode IN ({mode_marks}))")

    rows = conn.execute(
        f"""
        SELECT i.id, i.invoice_no, i.invoice_date, i.invoice_type, i.bill_name, i.grand_total,
               i.total_taxable, i.total_cgst + i.total_sgst AS gst,
               (SELECT coalesce(sum(p.amount), 0) FROM payment p WHERE p.invoice_id = i.id) AS received,
               {in_modes} AS received_in_modes,
               {loan_exists} AS has_loan,
               (SELECT group_concat(DISTINCT p.payment_mode) FROM payment p WHERE p.invoice_id = i.id) AS modes
        FROM invoice i
        WHERE {" AND ".join(where)}
        ORDER BY i.invoice_date, i.id
        """,
        [*mode_list, *args, *(mode_list if f.modes is not None else [])],
    ).fetchall()

    report = SalesReport(invoices=rows)
    for r in rows:
        report.count += 1
        report.sale_value += r["grand_total"]
        report.taxable += r["total_taxable"]
        report.gst += r["gst"]
        report.received += r["received"]
        report.received_in_modes += r["received_in_modes"]
        report.outstanding += max(r["grand_total"] - r["received"], 0)
        t = report.by_type.setdefault(r["invoice_type"], {"count": 0, "value": 0, "received": 0})
        t["count"] += 1
        t["value"] += r["grand_total"]
        t["received"] += r["received"]
    if rows:
        ids = [r["id"] for r in rows]
        for mode, amount in conn.execute(
            f"SELECT payment_mode, sum(amount) FROM payment WHERE invoice_id IN ({', '.join('?' * len(ids))})"
            f" AND payment_mode IN ({mode_marks}) GROUP BY payment_mode ORDER BY payment_mode",
            [*ids, *mode_list],
        ):
            report.by_mode[mode] = amount
    return report
