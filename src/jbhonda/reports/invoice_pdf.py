"""A4 GST tax invoice as PDF, for vehicle and part sales.

Everything printed comes from the invoice's own stored copy (customer, prices, tax), so a
reprint years later shows exactly what was billed. The only live data is the payment
history, so a reprint shows the current balance.
"""

import re
import sqlite3
from functools import cache
from importlib import resources
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from jbhonda import config
from jbhonda.core.invoices import InvoiceDetail, invoice_detail
from jbhonda.core.money import amount_in_words, format_inr
from jbhonda.core.settings import get_settings

FONT, BOLD = "DejaVuSans", "DejaVuSans-Bold"
GRID = colors.HexColor("#9aa5b1")
SHADE = colors.HexColor("#eef0f3")
PAGE_W, PAGE_H = A4
MARGIN = 12 * mm
WIDTH = PAGE_W - 2 * MARGIN

MODE_LABELS = {"CASH": "Cash", "UPI": "UPI", "CARD": "Card", "CHEQUE": "Cheque", "BANK_TRANSFER": "Bank transfer",
               "FINANCE": "Finance"}
POLICY_LABELS = {"COMPREHENSIVE": "Comprehensive", "THIRD_PARTY": "Third party",
                 "BUNDLED_1OD_5TP": "1 yr OD + 5 yr TP"}


@cache
def _register_fonts() -> None:
    # A bundled font with the ₹ sign, so the PDF looks the same on every PC.
    fonts = resources.files("jbhonda.assets").joinpath("fonts")
    pdfmetrics.registerFont(TTFont(FONT, str(fonts.joinpath("DejaVuSans.ttf"))))
    pdfmetrics.registerFont(TTFont(BOLD, str(fonts.joinpath("DejaVuSans-Bold.ttf"))))


def _style(size: float = 8, bold: bool = False, align: int | None = None, leading: float | None = None,
           color=colors.black) -> ParagraphStyle:
    return ParagraphStyle(
        "s", fontName=BOLD if bold else FONT, fontSize=size, leading=leading or size * 1.3,
        alignment=align if align is not None else 0, textColor=color,
    )


def _p(text: str | None, **style) -> Paragraph:
    return Paragraph(escape(text or "").replace("\n", "<br/>"), _style(**style))


def _money(paise: int) -> str:
    return format_inr(paise, symbol=False)


def _date(iso: str | None) -> str:
    if not iso:
        return ""
    y, m, d = iso[:10].split("-")
    return f"{d}-{m}-{y}"


def _month(yyyy_mm: str | None) -> str | None:
    if not yyyy_mm:
        return None
    y, m = yyyy_mm.split("-")
    return f"{('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())[int(m) - 1]} {y}"


def pdf_path(invoice_no: str) -> Path:
    """Where an invoice's PDF is kept: <data>/invoices/2026-27/JBH-V-2026-27-0001.pdf"""
    fy = re.search(r"\d{4}-\d{2}", invoice_no)
    folder = config.data_dir() / "invoices" / (fy.group(0) if fy else "other")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (re.sub(r"[^A-Za-z0-9-]+", "-", invoice_no).strip("-") + ".pdf")


def write_invoice_pdf(conn: sqlite3.Connection, invoice_id: int, path: Path | None = None) -> Path:
    """Render the invoice to PDF (default location: pdf_path) and return the file path."""
    _register_fonts()
    detail = invoice_detail(conn, invoice_id)
    shop = get_settings(conn)
    path = path or pdf_path(detail.invoice["invoice_no"])
    cancelled = detail.invoice["status"] == "CANCELLED"

    def decorate(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(FONT, 7)
        canvas.setFillColor(colors.HexColor("#52606d"))
        canvas.drawString(MARGIN, 7 * mm, "This is a computer generated invoice.")
        canvas.drawRightString(PAGE_W - MARGIN, 7 * mm, f"{detail.invoice['invoice_no']}  ·  page {doc.page}")
        if cancelled:
            canvas.setFont(BOLD, 80)
            canvas.setFillColor(colors.Color(0.8, 0, 0, alpha=0.18))
            canvas.translate(PAGE_W / 2, PAGE_H / 2)
            canvas.rotate(40)
            canvas.drawCentredString(0, 0, "CANCELLED")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=14 * mm,
        title=f"Invoice {detail.invoice['invoice_no']}", author=shop["shop_name"],
    )
    story = [
        _header(shop, detail),
        Spacer(1, 3 * mm),
        _parties(shop, detail),
        Spacer(1, 3 * mm),
        _items(detail),
        _totals(detail),
        Spacer(1, 3 * mm),
        _hsn_summary(detail),
    ]
    if detail.payments or detail.invoice["status"] == "ACTIVE":
        story += [Spacer(1, 3 * mm), _payments(detail)]
    if cancelled:
        story += [Spacer(1, 3 * mm), _p(
            f"CANCELLED on {_date(detail.invoice['cancelled_at'])}. Reason: {detail.invoice['cancel_reason'] or '-'}",
            size=9, bold=True, color=colors.HexColor("#cc0000"))]
    story += [Spacer(1, 4 * mm), _footer(shop)]
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path


# ------------------------------------------------------------------ sections

def _boxed(rows, col_widths, extra=()) -> Table:
    table = Table(rows, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        *extra,
    ]))
    return table


def _header(shop, detail: InvoiceDetail) -> Table:
    inv = detail.invoice
    address = ", ".join(x for x in (shop["address_line1"], shop["address_line2"]) if x)
    place = ", ".join(x for x in (shop["city"], shop["district"], shop["state"]) if x)
    if shop["pincode"]:
        place += f" - {shop['pincode']}"
    contact = "  ·  ".join(x for x in (f"Ph: {shop['phone']}" if shop["phone"] else "", shop["email"]) if x)
    left = [
        _p(shop["shop_name"], size=16, bold=True, leading=20),
        _p(address, size=8.5),
        _p(place, size=8.5),
        _p(contact, size=8.5),
        _p(f"GSTIN: {shop['gstin'] or '-'}" + (f"   State code: {shop['state_code']}" if shop["state_code"] else ""),
           size=8.5, bold=True),
    ]
    kind = {"VEHICLE": "Vehicle", "PART": "Parts", "SERVICE": "Service"}[inv["invoice_type"]]
    right = [
        _p("TAX INVOICE", size=14, bold=True, align=TA_RIGHT, leading=18),
        _p(f"{kind} sale", size=8.5, align=TA_RIGHT),
        Spacer(1, 2 * mm),
        _p(f"Invoice no: {inv['invoice_no']}", size=9, bold=True, align=TA_RIGHT),
        _p(f"Date: {_date(inv['invoice_date'])}", size=9, align=TA_RIGHT),
    ]
    if detail.booking_no:
        right.append(_p(f"Booking no: {detail.booking_no}", size=8.5, align=TA_RIGHT))
    if shop["state"]:
        right.append(_p(f"Place of supply: {shop['state']}", size=8.5, align=TA_RIGHT))
    return _boxed([[left, right]], [WIDTH * 0.62, WIDTH * 0.38])


def _parties(shop, detail: InvoiceDetail) -> Table:
    inv = detail.invoice
    bill_to = [_p("Bill to", size=7.5, color=colors.HexColor("#52606d")), _p(inv["bill_name"], size=10, bold=True)]
    for line in (inv["bill_relation"], inv["bill_address"],
                 f"Mobile: {inv['bill_phone']}" if inv["bill_phone"] else None,
                 f"GSTIN: {inv['bill_gstin']}" if inv["bill_gstin"] else None):
        if line:
            bill_to.append(_p(line, size=8.5))

    right: list = []
    v = detail.vehicle
    if v is not None:
        right.append(_p("Vehicle details", size=7.5, color=colors.HexColor("#52606d")))
        pairs = [("Chassis no", v["chassis_number"]), ("Engine no", v["engine_number"]), ("Colour", v["color"]),
                 ("Key no", v["key_number"]), ("Battery no", v["battery_number"]),
                 ("Mfg. month", _month(v["manufacturing_month"])), ("Reg. no", v["registration_number"])]
        right += [_p(f"{k}: {val}", size=8.5) for k, val in pairs if val]
    job = detail.job
    if job is not None:
        right.append(_p("Service details", size=7.5, color=colors.HexColor("#52606d")))
        kind = f"Free service {job['free_service_no']}" if job["service_type"] == "FREE" else job["service_type"].title()
        due = ", ".join(x for x in (_date(job["next_service_date"]),
                                    f"{job['next_service_km']:,} km" if job["next_service_km"] else "") if x)
        pairs = [("Job card", job["job_no"]), ("Vehicle", job["vehicle_description"]),
                 ("Reg. no", job["registration_number"]), ("Chassis no", job["chassis_number"]),
                 ("Odometer", f"{job['odometer_km']:,} km" if job["odometer_km"] is not None else None),
                 ("Service", kind), ("Mechanic", job["mechanic_name"]), ("Next service due", due)]
        right += [_p(f"{k}: {val}", size=8.5) for k, val in pairs if val]
        if job["work_done"]:
            right.append(_p(f"Work done: {job['work_done']}", size=8.5))
    if inv["hypothecated_to"]:
        right.append(_p(f"Hypothecated to: {inv['hypothecated_to']}", size=8.5, bold=True))
    if detail.insurance is not None:
        ins = detail.insurance
        right.append(_p(
            f"Insurance: {ins['insurer_name']}, policy {ins['policy_number']} "
            f"({POLICY_LABELS.get(ins['policy_type'], ins['policy_type'])}), "
            f"valid {_date(ins['start_date'])} to {_date(ins['end_date'])}", size=8.5))
    if not right:
        right = [_p("")]
    return _boxed([[bill_to, right]], [WIDTH * 0.5, WIDTH * 0.5], extra=[("LINEAFTER", (0, 0), (0, 0), 0.6, GRID)])


_ITEM_COLUMNS = (("#", 7), ("Description", 52), ("HSN/SAC", 16), ("Qty", 9), ("Rate", 19), ("Discount", 16),
                 ("Taxable", 20), ("GST %", 11), ("GST amt", 17), ("Amount", 19))


def _items(detail: InvoiceDetail) -> Table:
    right = {"Qty", "Rate", "Discount", "Taxable", "GST %", "GST amt", "Amount"}
    header = [_p(t, size=7.5, bold=True, align=TA_RIGHT if t in right else 0) for t, _ in _ITEM_COLUMNS]
    rows = [header]
    for item in detail.items:
        values = (
            str(item["line_no"]), item["description"], item["hsn_code"], str(item["quantity"]),
            _money(item["unit_price"]), _money(item["discount"]) if item["discount"] else "",
            _money(item["taxable_value"]), f"{item['gst_rate_bp'] / 100:g}", _money(item["cgst"] + item["sgst"]),
            _money(item["line_total"]),
        )
        rows.append([_p(v, size=8, align=TA_RIGHT if t in right else 0) for (t, _), v in zip(_ITEM_COLUMNS, values)])
    table = Table(rows, colWidths=[w * mm * WIDTH / (186 * mm) for _, w in _ITEM_COLUMNS], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("BACKGROUND", (0, 0), (-1, 0), SHADE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _totals(detail: InvoiceDetail) -> Table:
    inv = detail.invoice
    lines = [("Taxable value", inv["total_taxable"]), ("CGST", inv["total_cgst"]), ("SGST", inv["total_sgst"])]
    if inv["round_off"]:
        lines.append(("Round off", inv["round_off"]))
    figures = [[_p(label, size=8.5), _p(_money(value), size=8.5, align=TA_RIGHT)] for label, value in lines]
    figures.append([_p("Grand total", size=10.5, bold=True),
                    _p(format_inr(inv["grand_total"]), size=10.5, bold=True, align=TA_RIGHT)])
    inner = WIDTH * 0.4 - 8  # the box cell has 4pt padding each side
    figures_table = Table(figures, colWidths=[inner * 0.45, inner * 0.55])
    figures_table.setStyle(TableStyle([
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, GRID),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    words = [_p("Amount in words", size=7.5, color=colors.HexColor("#52606d")),
             _p(amount_in_words(inv["grand_total"]), size=9, bold=True)]
    return _boxed([[words, figures_table]], [WIDTH * 0.6, WIDTH * 0.4],
                  extra=[("LINEAFTER", (0, 0), (0, 0), 0.6, GRID)])


def _hsn_summary(detail: InvoiceDetail) -> Table:
    header = ["HSN/SAC", "Taxable value", "CGST rate", "CGST", "SGST rate", "SGST", "Total tax"]
    rows = [[_p(h, size=7.5, bold=True, align=TA_RIGHT if i else 0) for i, h in enumerate(header)]]
    for r in detail.hsn_summary:
        half = f"{r.gst_rate_bp / 200:g}%"
        values = (r.hsn_code, _money(r.taxable), half, _money(r.cgst), half, _money(r.sgst), _money(r.cgst + r.sgst))
        rows.append([_p(v, size=8, align=TA_RIGHT if i else 0) for i, v in enumerate(values)])
    table = Table(rows, colWidths=[WIDTH / 7] * 7)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("BACKGROUND", (0, 0), (-1, 0), SHADE),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _payments(detail: InvoiceDetail) -> KeepTogether:
    parts: list = [_p("Payments received", size=9, bold=True)]
    if detail.payments:
        rows = [[_p(h, size=7.5, bold=True, align=TA_RIGHT if h == "Amount" else 0)
                 for h in ("Receipt no", "Date", "Mode", "Reference", "Amount")]]
        for p in detail.payments:
            rows.append([
                _p(p["receipt_no"], size=8), _p(_date(p["payment_date"]), size=8),
                _p(("Refund - " if p["amount"] < 0 else "") + MODE_LABELS.get(p["payment_mode"], p["payment_mode"]),
                   size=8),
                _p(p["reference_no"], size=8), _p(_money(p["amount"]), size=8, align=TA_RIGHT),
            ])
        table = Table(rows, colWidths=[WIDTH * w for w in (0.24, 0.14, 0.18, 0.26, 0.18)])
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, GRID),
            ("BACKGROUND", (0, 0), (-1, 0), SHADE),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        parts.append(table)
    balance = detail.balance
    summary = f"Paid: {format_inr(detail.paid)}  ·  Balance due: {format_inr(max(balance, 0))}"
    if balance < 0:
        summary += f"  ·  Refund due: {format_inr(-balance)}"
    parts.append(_p(summary, size=9, bold=True, align=TA_RIGHT))
    if detail.loan is not None and detail.loan["status"] != "DISBURSED":
        parts.append(_p(
            f"Loan of {format_inr(detail.loan['loan_amount'])} from {detail.loan['financer_name']} "
            "awaiting disbursement.", size=8, align=TA_RIGHT))
    return KeepTogether(parts)


def _footer(shop) -> KeepTogether:
    bank = [_p("Bank details", size=7.5, color=colors.HexColor("#52606d"))]
    if shop["bank_name"] or shop["bank_account_no"]:
        bank += [_p(shop["bank_name"], size=8.5), _p(f"A/c no: {shop['bank_account_no'] or '-'}", size=8.5),
                 _p(f"IFSC: {shop['bank_ifsc'] or '-'}", size=8.5)]
    terms = [_p("Terms & conditions", size=7.5, color=colors.HexColor("#52606d")),
             _p(shop["invoice_terms"] or "", size=7.5)]
    top = _boxed([[bank, terms]], [WIDTH * 0.4, WIDTH * 0.6], extra=[("LINEAFTER", (0, 0), (0, 0), 0.6, GRID)])
    signatures = Table(
        [[_p("Customer's signature", size=8.5), _p(f"For {shop['shop_name']}", size=8.5, bold=True, align=TA_RIGHT)],
         [Spacer(1, 12 * mm), Spacer(1, 12 * mm)],
         [_p("", size=8.5), _p("Authorised signatory", size=8.5, align=TA_RIGHT)]],
        colWidths=[WIDTH * 0.5, WIDTH * 0.5],
    )
    return KeepTogether([top, Spacer(1, 3 * mm), signatures])
