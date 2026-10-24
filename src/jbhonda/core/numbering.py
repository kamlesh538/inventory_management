"""Document numbers: JBH/V/2026-27/0001. Restart at 1 every financial year (April-March).

Numbers are taken inside the caller's transaction, so a sale that fails rolls its number
back too and the series stays gapless, as GST rules require for invoices.
"""

import sqlite3
from datetime import date

from jbhonda.db import NOW

DEFAULT_PREFIX = {
    "VEHICLE": "JBH/V/",
    "PART": "JBH/P/",
    "BOOKING": "JBH/B/",
    "RECEIPT": "JBH/R/",
}


def financial_year(on: date) -> str:
    start = on.year if on.month >= 4 else on.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def next_number(conn: sqlite3.Connection, series: str, on: date) -> str:
    if not conn.in_transaction:
        raise RuntimeError("next_number must be called inside a transaction")
    fy = financial_year(on)
    row = conn.execute(
        f"""
        INSERT INTO invoice_sequence (series, financial_year, prefix, last_number)
        VALUES (?, ?, ?, 1)
        ON CONFLICT (series, financial_year)
        DO UPDATE SET last_number = last_number + 1, updated_at = {NOW}
        RETURNING prefix, last_number
        """,
        (series, fy, DEFAULT_PREFIX[series]),
    ).fetchone()
    return f"{row['prefix']}{fy}/{row['last_number']:04d}"
