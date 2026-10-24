from datetime import date, timedelta

import pytest
from PySide6.QtPdf import QPdfDocument

from jbhonda.core.customers import save_customer
from jbhonda.core.inventory import save_part, save_vehicle, save_vehicle_model
from jbhonda.core.invoices import find_invoices, invoice_detail
from jbhonda.core.masters import save_financer
from jbhonda.core.sales import PartLine, PaymentIn, balance_due, cancel_invoice, complete_part_sale
from jbhonda.core.settings import update_settings
from jbhonda.core.vehicle_sale import LoanIn, complete_vehicle_sale
from jbhonda.reports.invoice_pdf import pdf_path, write_invoice_pdf
from jbhonda.ui import widgets
from jbhonda.ui.invoices_page import CancelInvoiceDialog, InvoicesPage, LoanReceivedDialog, ReceivePaymentDialog

TODAY = date.today()


@pytest.fixture
def sales(conn):
    update_settings(conn, gstin="27AAPFU0939F1ZV", city="Agra", bank_name="SBI", invoice_terms="No returns.")
    model = save_vehicle_model(conn, model_name="Activa 6G", category="SCOOTER", gst_rate_bp=1800,
                               ex_showroom_price=8500000)
    vehicle = save_vehicle(conn, vehicle_model_id=model, chassis_number="ME4CH1", engine_number="EN1",
                           color="White", key_number="K77")
    customer = save_customer(conn, name="Ramesh Kumar", phone="9876543210", relation_type="S/O",
                             relation_name="Suresh")
    hdfc = save_financer(conn, name="HDFC Bank")
    oil = save_part(conn, part_number="OIL", name="Engine Oil", hsn_code="2710", gst_rate_bp=1800,
                    sale_price=40000, opening_qty=10)
    plug = save_part(conn, part_number="PLUG", name="Spark Plug", hsn_code="8511", gst_rate_bp=2800,
                     sale_price=14000, opening_qty=10)
    v_inv = complete_vehicle_sale(conn, customer_id=customer, vehicle_id=vehicle, payments=[PaymentIn("CASH", 1500000)],
                                  loan=LoanIn(financer_id=hdfc, loan_amount=7000000), on=TODAY)
    p_inv = complete_part_sale(conn, lines=[PartLine(oil, 2), PartLine(plug, 1)],
                               payments=[PaymentIn("CASH", 94000)], on=TODAY - timedelta(days=40))
    return {"vehicle_invoice": v_inv, "part_invoice": p_inv, "customer": customer}


def _pdf_text(path) -> str:
    doc = QPdfDocument()
    doc.load(str(path))
    assert doc.pageCount() >= 1
    return " ".join(doc.getAllText(i).text() for i in range(doc.pageCount()))


# ---------------------------------------------------------------- core

def test_find_invoices_filters(conn, sales):
    def numbers(**kw):
        return [r["invoice_no"].split("/")[1] for r in find_invoices(conn, **kw)]

    assert numbers() == ["V", "P"]  # newest first
    assert numbers(invoice_type="PART") == ["P"]
    assert numbers(text="ME4CH1") == ["V"]  # by chassis
    assert numbers(text="ramesh") == ["V"]
    assert numbers(status="DUE") == ["V"]  # loan outstanding
    assert numbers(date_from=TODAY - timedelta(days=7)) == ["V"]
    row = find_invoices(conn, invoice_type="VEHICLE")[0]
    assert (row["paid"], row["balance"], row["loan_status"]) == (1500000, 7000000, "SANCTIONED")

    cancel_invoice(conn, sales["part_invoice"], "test")
    assert numbers(status="REFUND") == ["P"]
    assert numbers(status="CANCELLED") == ["P"]


def test_hsn_summary_groups_by_code_and_rate(conn, sales):
    detail = invoice_detail(conn, sales["part_invoice"])
    assert [(r.hsn_code, r.gst_rate_bp) for r in detail.hsn_summary] == [("2710", 1800), ("8511", 2800)]
    assert sum(r.taxable + r.cgst + r.sgst for r in detail.hsn_summary) + detail.invoice["round_off"] == \
        detail.invoice["grand_total"]


# ---------------------------------------------------------------- PDF

def test_vehicle_invoice_pdf_contents(conn, sales):
    path = write_invoice_pdf(conn, sales["vehicle_invoice"])
    assert path == pdf_path(conn.execute("SELECT invoice_no FROM invoice WHERE id = ?",
                                         (sales["vehicle_invoice"],)).fetchone()[0])
    assert path.read_bytes().startswith(b"%PDF")
    text = _pdf_text(path)
    for expected in ("TAX INVOICE", "Jai Bajrang Bali Honda", "27AAPFU0939F1ZV", "Ramesh Kumar", "S/O Suresh",
                     "ME4CH1", "K77", "Hypothecated to: HDFC Bank", "Rupees Eighty Five Thousand Only",
                     "₹85,000.00", "awaiting disbursement", "No returns."):
        assert expected in text, expected


def test_cancelled_invoice_pdf_is_marked(conn, sales, tmp_path):
    cancel_invoice(conn, sales["part_invoice"], "billed twice")
    text = _pdf_text(write_invoice_pdf(conn, sales["part_invoice"], tmp_path / "x.pdf"))
    assert "CANCELLED" in text and "billed twice" in text and "Refund due" in text
    assert "Cash Customer" in text and "28%" not in text  # GST shown as split rates: 14% + 14%
    assert "14%" in text


# ---------------------------------------------------------------- screen

@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(widgets, "show_error", lambda parent, msg: shown.append(("error", msg)))
    monkeypatch.setattr(widgets, "show_info", lambda parent, msg: shown.append(("info", msg)))
    monkeypatch.setattr(widgets, "confirm", lambda parent, msg: True)
    return shown


@pytest.fixture
def page(qtbot, conn, sales):
    p = InvoicesPage(conn)
    qtbot.addWidget(p)
    p.period.setValue("ALL")
    return p


def _select(page, kind):
    for r in range(page.table.rowCount()):
        if page.table.item(r, 2).text() == kind:
            page.table.selectRow(r)
            return page.selected()


def test_buttons_follow_invoice_state(page):
    _select(page, "Vehicle")
    assert page.receive_button.isEnabled() and page.loan_button.isEnabled() and not page.refund_button.isEnabled()
    _select(page, "Parts")
    assert not page.receive_button.isEnabled() and not page.loan_button.isEnabled()
    assert page.footer.text().startswith("2 invoices")


def test_period_filter_defaults_to_last_30_days(qtbot, conn, sales):
    p = InvoicesPage(conn)
    qtbot.addWidget(p)
    assert p.table.rowCount() == 1  # the parts bill is 40 days old


def test_loan_received_then_settled(qtbot, conn, page, sales, messages):
    inv = _select(page, "Vehicle")
    d = LoanReceivedDialog(conn, inv["loan_id"])
    qtbot.addWidget(d)
    d.reference.setText("NEFT55")
    d.accept()
    assert messages == []
    assert balance_due(conn, sales["vehicle_invoice"]) == 0


def test_receive_balance(qtbot, conn, sales, messages):
    d = ReceivePaymentDialog(conn, sales["vehicle_invoice"])
    qtbot.addWidget(d)
    assert d.amount.text() == "70000.00"  # defaults to the balance
    d.amount.setText("5000")
    d.accept()
    assert balance_due(conn, sales["vehicle_invoice"]) == 6500000
    assert conn.execute("SELECT payment_type FROM payment ORDER BY id DESC").fetchone()[0] == "BALANCE"


def test_cancel_then_refund(qtbot, conn, page, sales, messages):
    d = CancelInvoiceDialog(conn, sales["part_invoice"])
    qtbot.addWidget(d)
    d.accept()
    assert "reason" in messages[-1][1]
    d.reason.setText("wrong customer")
    d.accept()
    page.refresh()
    _select(page, "Parts")
    assert page.refund_button.isEnabled() and not page.cancel_button.isEnabled()
    refund = ReceivePaymentDialog(conn, sales["part_invoice"], refund=True)
    qtbot.addWidget(refund)
    assert refund.amount.text() == "940.00"
    refund.accept()
    assert balance_due(conn, sales["part_invoice"]) == 0


def test_open_pdf_writes_and_opens(page, opened_files):
    _select(page, "Vehicle")
    page.open_pdf()
    assert len(opened_files) == 1 and opened_files[0].suffix == ".pdf" and opened_files[0].exists()


@pytest.mark.parametrize(("choice", "printed", "opened"), [("Print", 1, 0), ("View PDF", 0, 1), ("Close", 0, 0)])
def test_sale_offers_to_print(qtbot, conn, messages, opened_files, printed_files, monkeypatch, choice, printed, opened):
    from jbhonda.ui.sell_parts_page import SellPartsPage

    monkeypatch.setattr(widgets, "choose", lambda parent, title, message, options: choice)
    oil = save_part(conn, part_number="OIL2", name="Oil", hsn_code="2710", gst_rate_bp=1800, sale_price=40000,
                    opening_qty=1)
    p = SellPartsPage(conn)
    qtbot.addWidget(p)
    p.add_part(oil)
    assert p.create_bill() is not None
    assert (len(printed_files), len(opened_files)) == (printed, opened)


def test_print_button(page, printed_files):
    _select(page, "Vehicle")
    page.print_selected()
    assert len(printed_files) == 1 and printed_files[0].exists()


def test_render_to_printer_produces_every_page(qtbot, conn, sales, tmp_path):
    """The real printing path, with a PDF 'printer' instead of paper."""
    from PySide6.QtPrintSupport import QPrinter

    from jbhonda.ui.printing import render_to_printer

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "printed.pdf"))
    pages = render_to_printer(write_invoice_pdf(conn, sales["vehicle_invoice"]), printer)
    assert pages == 1
    doc = QPdfDocument()
    doc.load(str(tmp_path / "printed.pdf"))
    assert doc.pageCount() == 1
