from datetime import date

import pytest

from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import adjust_stock, find_parts, part_history, purchase_part, save_part
from jbhonda.core.sales import PartLine, cancel_invoice, sell_parts
from jbhonda.ui import widgets
from jbhonda.ui.parts_page import AdjustStockDialog, PartDialog, PartHistoryDialog, PartsPage, StockInDialog
from jbhonda.ui.widgets import DataTable


@pytest.fixture
def parts(conn):
    plug = save_part(conn, part_number="31917-KRM-841", name="Spark Plug", hsn_code="8511", gst_rate_bp=1800,
                     mrp=15000, sale_price=14000, opening_qty=20, opening_unit_cost=9000, reorder_level=5,
                     rack_location="a-1")
    oil = save_part(conn, part_number="08232-P99", name="Engine Oil 0.8L", category="OIL", hsn_code="2710",
                    gst_rate_bp=1800, sale_price=40000, opening_qty=2, opening_unit_cost=28000, reorder_level=6)
    return {"plug": plug, "oil": oil}


def _part(conn, part_id):
    return conn.execute("SELECT * FROM part WHERE id = ?", (part_id,)).fetchone()


# ---------------------------------------------------------------- core

def test_edit_part_keeps_stock(conn, parts):
    save_part(conn, part_id=parts["plug"], part_number="31917-KRM-841", name="Spark Plug (NGK)", hsn_code="8511",
              gst_rate_bp=1800, mrp=16000, sale_price=15500)
    p = _part(conn, parts["plug"])
    assert (p["name"], p["sale_price"], p["stock_qty"], p["rack_location"]) == ("Spark Plug (NGK)", 15500, 20, None)
    with pytest.raises(ValueError):
        save_part(conn, part_id=parts["plug"], part_number="X", name="X", hsn_code="1", gst_rate_bp=0,
                  sale_price=1, opening_qty=5)


def test_price_cannot_exceed_mrp(conn, parts):
    with pytest.raises(BusinessError, match="MRP"):
        save_part(conn, part_number="NEW", name="New", hsn_code="8714", gst_rate_bp=1800, mrp=1000, sale_price=1001)
    with pytest.raises(BusinessError, match="MRP"):
        sell_parts(conn, lines=[PartLine(parts["plug"], 1, unit_price=15001)])
    sell_parts(conn, lines=[PartLine(parts["plug"], 1, unit_price=15000)])
    sell_parts(conn, lines=[PartLine(parts["oil"], 1, unit_price=99999)])  # no MRP set: no limit


def test_find_parts(conn, parts):
    def numbers(**kw):
        return [r["part_number"] for r in find_parts(conn, **kw)]

    assert numbers() == ["08232-P99", "31917-KRM-841"]  # by name
    assert numbers(text="A-1") == ["31917-KRM-841"]  # rack location
    assert numbers(category="OIL") == ["08232-P99"]
    assert numbers(low_stock_only=True) == ["08232-P99"]
    from jbhonda.core.masters import set_active
    set_active(conn, "part", parts["oil"], False)
    assert numbers() == ["31917-KRM-841"]
    assert numbers(include_inactive=True) == ["08232-P99", "31917-KRM-841"]


def test_history_has_running_balance_and_invoice(conn, parts):
    purchase_part(conn, part_id=parts["plug"], quantity=10, unit_cost=9500, reference_no="HMSI-9", on=date(2026, 10, 2))
    inv = sell_parts(conn, lines=[PartLine(parts["plug"], 3)], walk_in_name="Mohan", on=date(2026, 10, 3))
    cancel_invoice(conn, inv, "billed by mistake", on=date(2026, 10, 3))
    adjust_stock(conn, part_id=parts["plug"], quantity_change=-1, reason="Damaged", on=date(2026, 10, 4))
    rows = part_history(conn, parts["plug"])
    assert [(r["movement_type"], r["quantity"], r["balance"]) for r in rows] == [
        ("PURCHASE", 20, 20), ("PURCHASE", 10, 30), ("SALE", -3, 27), ("SALE_CANCEL", 3, 30), ("ADJUSTMENT", -1, 29),
    ]
    assert rows[2]["invoice_no"] == "JBH/P/2026-27/0001" and rows[2]["bill_name"] == "Mohan"
    assert _part(conn, parts["plug"])["stock_qty"] == 29


# ---------------------------------------------------------------- screens

@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(widgets, "show_error", lambda parent, msg: shown.append(msg))
    monkeypatch.setattr(widgets, "confirm", lambda parent, msg: True)
    return shown


def test_add_part_dialog_with_opening_stock(qtbot, conn, messages):
    d = PartDialog(conn)
    qtbot.addWidget(d)
    d.part_number.setText("06455-kwn-901")
    d.name.setText("Brake Shoe Set")
    d.unit.setValue("SET")
    qtbot.keyClicks(d.mrp, "450")
    assert d.sale_price.text() == "450"  # defaults to MRP
    d.sale_price.clear()
    qtbot.keyClicks(d.sale_price, "420")
    qtbot.keyClicks(d.mrp, "0")  # MRP 4500 no longer overwrites a typed sale price
    assert d.sale_price.text() == "420"
    d.mrp.setText("450")
    d.reorder_level.setValue(3)
    d.opening_qty.setValue(8)
    d.opening_cost.setText("300")
    d.accept()
    assert messages == []
    p = conn.execute("SELECT * FROM part").fetchone()
    assert (p["part_number"], p["unit"], p["sale_price"], p["stock_qty"], p["avg_cost"]) == (
        "06455-KWN-901", "SET", 42000, 8, 30000)


def test_stock_in_dialog(qtbot, conn, parts, messages):
    d = StockInDialog(conn, parts["plug"])
    qtbot.addWidget(d)
    assert d.unit_cost.text() == "90.00"  # defaults to current average cost
    d.quantity.setValue(20)
    d.unit_cost.setText("")
    qtbot.keyClicks(d.unit_cost, "110")
    assert d.gst.text() == "396.00"  # 18% of 20 x 110
    d.bill_no.setText("hmsi-55")
    d.accept()
    assert messages == []
    p = _part(conn, parts["plug"])
    assert (p["stock_qty"], p["avg_cost"]) == (40, 10000)  # (20x90 + 20x110) / 40
    last = part_history(conn, parts["plug"])[-1]
    assert (last["reference_no"], last["gst_amount"]) == ("HMSI-55", 39600)


def test_adjust_dialog_requires_reason_and_limits_range(qtbot, conn, parts, messages):
    d = AdjustStockDialog(conn, parts["oil"])
    qtbot.addWidget(d)
    assert d.change.minimum() == -2
    d.change.setValue(-1)
    assert d.result_label.text() == "1 pcs"
    d.accept()
    assert "reason" in messages[-1]
    d.reason.setCurrentText("Damaged")
    d.accept()
    assert _part(conn, parts["oil"])["stock_qty"] == 1


def test_history_dialog(qtbot, conn, parts):
    sell_parts(conn, lines=[PartLine(parts["plug"], 2)])
    d = PartHistoryDialog(conn, parts["plug"])
    qtbot.addWidget(d)
    assert d.table.rowCount() == 2
    assert d.table.item(1, 2).text() == "-2" and d.table.item(1, 6).text() == "18"


def test_parts_page_highlights_low_stock(qtbot, conn, parts, messages):
    page = PartsPage(conn)
    qtbot.addWidget(page)
    assert page.table.rowCount() == 2
    oil_row = 0  # sorted by name: "Engine Oil" before "Spark Plug"
    assert page.table.item(oil_row, 0).background().color() == DataTable.HIGHLIGHT
    assert page.table.item(1, 0).background().color() != DataTable.HIGHLIGHT
    assert page.footer.text() == "2 parts shown  ·  1 low on stock  ·  stock value at cost ₹2,360.00"

    page.table.selectRow(oil_row)
    assert page.stock_in_button.isEnabled()
    page.toggle_active()
    assert page.table.rowCount() == 1
