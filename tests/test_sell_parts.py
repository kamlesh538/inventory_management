import pytest
from PySide6.QtCore import Qt

from jbhonda.core.customers import save_customer
from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import part_by_number, save_part
from jbhonda.core.sales import PartLine, PaymentIn, balance_due, complete_part_sale
from jbhonda.ui import widgets
from jbhonda.ui.sell_parts_page import SellPartsPage


@pytest.fixture
def parts(conn):
    return {
        "plug": save_part(conn, part_number="31917-KRM-841", name="Spark Plug", hsn_code="8511", gst_rate_bp=1800,
                          mrp=15000, sale_price=14000, opening_qty=5),
        "oil": save_part(conn, part_number="08232-P99", name="Engine Oil", category="OIL", hsn_code="2710",
                         gst_rate_bp=1800, sale_price=40050, opening_qty=10),
        "empty": save_part(conn, part_number="EMPTY-1", name="Out Of Stock Thing", hsn_code="8714",
                           gst_rate_bp=1800, sale_price=100),
        "ramesh": save_customer(conn, name="Ramesh Kumar", phone="9876543210"),
    }


def _stock(conn, part_id):
    return conn.execute("SELECT stock_qty FROM part WHERE id = ?", (part_id,)).fetchone()[0]


# ---------------------------------------------------------------- core

def test_part_sale_with_split_payment(conn, parts):
    inv = complete_part_sale(
        conn, lines=[PartLine(parts["plug"], 2), PartLine(parts["oil"], 1)],
        payments=[PaymentIn("CASH", 50000), PaymentIn("UPI", 18100, "UTR1")],  # ₹680.50 rounds to ₹681
    )
    assert balance_due(conn, inv) == 0
    types = {r[0] for r in conn.execute("SELECT payment_type FROM payment")}
    assert types == {"FULL_PAYMENT"}


def test_walk_in_must_pay_in_full_but_customer_can_take_credit(conn, parts):
    with pytest.raises(BusinessError, match="walk-in"):
        complete_part_sale(conn, lines=[PartLine(parts["plug"], 1)], payments=[PaymentIn("CASH", 10000)])
    assert _stock(conn, parts["plug"]) == 5  # nothing saved
    inv = complete_part_sale(conn, lines=[PartLine(parts["plug"], 1)], payments=[PaymentIn("CASH", 10000)],
                             customer_id=parts["ramesh"])
    assert balance_due(conn, inv) == 4000


def test_overpayment_refused(conn, parts):
    with pytest.raises(BusinessError, match="more than the bill"):
        complete_part_sale(conn, lines=[PartLine(parts["plug"], 1)], payments=[PaymentIn("CASH", 14100)])


def test_part_by_number_is_exact_and_case_insensitive(conn, parts):
    assert part_by_number(conn, " 31917-krm-841 ")["id"] == parts["plug"]
    assert part_by_number(conn, "31917") is None


# ---------------------------------------------------------------- screen

@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(widgets, "show_error", lambda parent, msg: shown.append(("error", msg)))
    monkeypatch.setattr(widgets, "show_info", lambda parent, msg: shown.append(("info", msg)))
    monkeypatch.setattr(widgets, "confirm", lambda parent, msg: True)
    return shown


@pytest.fixture
def page(qtbot, conn, parts, messages):
    p = SellPartsPage(conn)
    qtbot.addWidget(p)
    return p


def _scan(qtbot, page, text):
    page.lookup.setText(text)
    qtbot.keyClick(page.lookup, Qt.Key.Key_Return)


def test_scan_adds_and_increments(qtbot, page, parts, messages):
    _scan(qtbot, page, "31917-krm-841")
    _scan(qtbot, page, "31917-KRM-841")
    assert len(page.lines) == 1 and page.lines[0].qty.value() == 2
    _scan(qtbot, page, "engine oil")  # unique name match
    assert len(page.lines) == 2
    assert page.lookup.text() == ""
    assert page.total_labels["total"].text() == "₹681.00"  # 2 x 140 + 400.50, rounded
    # Single payment row follows the bill until typed into
    assert page.payment_rows[0].amount.text() == "681.00"
    assert page.total_labels["balance"].text() == "₹0.00"


def test_out_of_stock_and_unknown_parts(qtbot, page, parts, messages):
    assert not any("EMPTY-1" in label for label in page._label_to_part)
    _scan(qtbot, page, "EMPTY-1")
    assert messages[-1] == ("error", "EMPTY-1 is out of stock.")
    _scan(qtbot, page, "zzz")
    assert "No single part" in messages[-1][1]
    assert len(page.lines) == 0


def test_cannot_add_beyond_stock(qtbot, page, parts, messages):
    for _ in range(6):
        page.add_part(parts["plug"])
    assert page.lines[0].qty.value() == 5
    assert messages[-1] == ("error", "Only 5 of 31917-KRM-841 in stock.")


def test_create_bill_walk_in(qtbot, conn, page, parts, messages):
    page.add_part(parts["plug"])
    page.lines[0].discount.setText("10")
    page.walk_in_name.setText("Mohan")
    page.walk_in_phone.setText("+91 91234 56789")
    invoice_id = page.create_bill()
    assert invoice_id is not None, messages
    inv = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
    assert (inv["bill_name"], inv["bill_phone"], inv["grand_total"]) == ("Mohan", "9123456789", 13000)
    assert balance_due(conn, invoice_id) == 0
    assert _stock(conn, parts["plug"]) == 4
    assert page.lines == [] and page.table.rowCount() == 0


def test_typed_payment_is_kept_and_credit_needs_customer(qtbot, conn, page, parts, messages):
    page.add_part(parts["oil"])
    page.payment_rows[0].amount.selectAll()
    qtbot.keyClicks(page.payment_rows[0].amount, "200")  # cashier types over the amount: a part payment
    page.add_part(parts["oil"])
    assert page.payment_rows[0].amount.text() == "200"  # not overwritten any more
    assert page.create_bill() is None
    assert "walk-in" in messages[-1][1]

    page.customer.set_customer(parts["ramesh"])
    assert not page.walk_in_name.isEnabled()
    invoice_id = page.create_bill()
    assert balance_due(conn, invoice_id) == 80100 - 20000


def test_remove_line(qtbot, page, parts):
    page.add_part(parts["plug"])
    page.add_part(parts["oil"])
    page.remove_line(page.lines[0])
    assert [line.part["part_number"] for line in page.lines] == ["08232-P99"]
    assert page.table.rowCount() == 1 and page.table.item(0, 0).text() == "08232-P99"
