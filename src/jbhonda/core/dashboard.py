import sqlite3
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Summary:
    vehicles_in_stock: int
    vehicles_booked: int
    low_stock_parts: int
    sales_today: int        # paise
    sales_this_month: int   # paise
    vehicles_sold_this_month: int


def summary(conn: sqlite3.Connection, today: date | None = None) -> Summary:
    today = today or date.today()
    month_start = today.replace(day=1).isoformat()

    def one(sql: str, *args) -> int:
        return conn.execute(sql, args).fetchone()[0]

    return Summary(
        vehicles_in_stock=one("SELECT count(*) FROM vehicle WHERE status = 'IN_STOCK'"),
        vehicles_booked=one("SELECT count(*) FROM vehicle WHERE status = 'BOOKED'"),
        low_stock_parts=one("SELECT count(*) FROM part WHERE is_active = 1 AND stock_qty <= reorder_level"),
        sales_today=one(
            "SELECT coalesce(sum(grand_total), 0) FROM invoice WHERE status = 'ACTIVE' AND invoice_date = ?",
            today.isoformat(),
        ),
        sales_this_month=one(
            "SELECT coalesce(sum(grand_total), 0) FROM invoice"
            " WHERE status = 'ACTIVE' AND invoice_date BETWEEN ? AND ?",
            month_start, today.isoformat(),
        ),
        vehicles_sold_this_month=one(
            "SELECT count(*) FROM invoice WHERE status = 'ACTIVE' AND invoice_type = 'VEHICLE'"
            " AND invoice_date BETWEEN ? AND ?",
            month_start, today.isoformat(),
        ),
    )
