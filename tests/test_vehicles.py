from datetime import date, timedelta

import pytest

from jbhonda.core.customers import add_customer
from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import find_vehicles, save_vehicle, save_vehicle_model, vehicle_colors
from jbhonda.core.sales import cancel_invoice, sell_vehicle
from jbhonda.ui import widgets
from jbhonda.ui.vehicles_page import VehicleDialog, VehiclesPage


@pytest.fixture
def stock(conn):
    activa = save_vehicle_model(conn, model_name="Activa 6G", variant="DLX", category="SCOOTER",
                                gst_rate_bp=1800, ex_showroom_price=8500000)
    shine = save_vehicle_model(conn, model_name="Shine 100", category="MOTORCYCLE",
                               gst_rate_bp=1800, ex_showroom_price=6800000)
    v1 = save_vehicle(conn, vehicle_model_id=activa, chassis_number="CH1", engine_number="EN1", color="Pearl White",
                      purchase_date=date(2026, 10, 1), purchase_taxable_value=6800000, purchase_gst_amount=1224000)
    v2 = save_vehicle(conn, vehicle_model_id=activa, chassis_number="CH2", engine_number="EN2", color="Matte Grey")
    v3 = save_vehicle(conn, vehicle_model_id=shine, chassis_number="CH3", engine_number="EN3", color="Black")
    customer = add_customer(conn, name="Ramesh Kumar", phone="9876543210")
    return {"activa": activa, "shine": shine, "v1": v1, "v2": v2, "v3": v3, "customer": customer}


def _edit(conn, vehicle_id, **changes):
    v = conn.execute("SELECT * FROM vehicle WHERE id = ?", (vehicle_id,)).fetchone()
    fields = {k: v[k] for k in (
        "vehicle_model_id", "chassis_number", "engine_number", "color", "key_number", "battery_number",
        "manufacturing_month", "purchase_invoice_no", "purchase_taxable_value", "purchase_gst_amount",
        "registration_number", "notes",
    )}
    fields["purchase_date"] = date.fromisoformat(v["purchase_date"]) if v["purchase_date"] else None
    return save_vehicle(conn, vehicle_id=vehicle_id, **{**fields, **changes})


# ---------------------------------------------------------------- core

def test_edit_unsold_vehicle(conn, stock):
    _edit(conn, stock["v2"], chassis_number="ch2-fixed", color="Red")
    row = conn.execute("SELECT chassis_number, color FROM vehicle WHERE id = ?", (stock["v2"],)).fetchone()
    assert tuple(row) == ("CH2-FIXED", "Red")


def test_edit_cannot_duplicate_another_vehicle(conn, stock):
    with pytest.raises(BusinessError, match="engine number EN1"):
        _edit(conn, stock["v2"], engine_number="en1")


def test_sold_vehicle_only_registration_and_notes_editable(conn, stock):
    invoice = sell_vehicle(conn, customer_id=stock["customer"], vehicle_id=stock["v1"])
    _edit(conn, stock["v1"], registration_number="up80 ab 1234", notes="RC delivered")
    assert conn.execute("SELECT registration_number FROM vehicle WHERE id = ?", (stock["v1"],)).fetchone()[0] == "UP80 AB 1234"
    with pytest.raises(BusinessError, match="cancel its invoice"):
        _edit(conn, stock["v1"], color="Red")
    cancel_invoice(conn, invoice, "wrong colour billed")
    _edit(conn, stock["v1"], color="Red")


def test_validation(conn, stock):
    with pytest.raises(BusinessError, match="Manufacturing month"):
        _edit(conn, stock["v2"], manufacturing_month="2026-13")
    with pytest.raises(BusinessError, match="future"):
        _edit(conn, stock["v2"], purchase_date=date.today() + timedelta(days=1))
    with pytest.raises(BusinessError, match="Colour"):
        _edit(conn, stock["v2"], color=" ")


def test_find_vehicles_filters(conn, stock):
    sell_vehicle(conn, customer_id=stock["customer"], vehicle_id=stock["v1"], on=date(2026, 10, 20))

    def chassis(**kw):
        return [r["chassis_number"] for r in find_vehicles(conn, **kw)]

    assert chassis() == ["CH3", "CH2", "CH1"]  # in stock first, sold last
    assert chassis(status="IN_STOCK") == ["CH3", "CH2"]
    assert chassis(vehicle_model_id=stock["activa"]) == ["CH2", "CH1"]
    assert chassis(color="black") == ["CH3"]
    assert chassis(text="ramesh") == ["CH1"]  # by customer name
    assert chassis(text="shine") == ["CH3"]
    sold = find_vehicles(conn, status="SOLD")[0]
    assert (sold["invoice_no"], sold["bill_name"]) == ("JBH/V/2026-27/0001", "Ramesh Kumar")
    assert vehicle_colors(conn) == ["Black", "Matte Grey", "Pearl White"]


def test_cancelled_sale_not_shown_as_sold(conn, stock):
    invoice = sell_vehicle(conn, customer_id=stock["customer"], vehicle_id=stock["v1"])
    cancel_invoice(conn, invoice, "test")
    row = [r for r in find_vehicles(conn) if r["id"] == stock["v1"]][0]
    assert (row["status"], row["invoice_no"]) == ("IN_STOCK", None)


# ---------------------------------------------------------------- screens

@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(widgets, "show_error", lambda parent, msg: shown.append(msg))
    return shown


def test_add_vehicle_dialog_with_gst_autofill(qtbot, conn, stock, messages):
    d = VehicleDialog(conn)
    qtbot.addWidget(d)
    d.model.setValue(stock["shine"])
    qtbot.keyClicks(d.chassis, "me4jc65")
    assert d.chassis.text() == "ME4JC65"  # typed in capitals
    d.engine.setText("JC65E1")
    d.color.setCurrentText("Black")
    qtbot.keyClicks(d.taxable, "50000")
    assert d.gst.text() == "9000.00"  # 18% of 50,000
    assert d.total.text() == "₹59,000.00"
    d.accept()
    assert messages == []
    row = conn.execute("SELECT * FROM vehicle WHERE chassis_number = 'ME4JC65'").fetchone()
    assert (row["purchase_taxable_value"], row["purchase_gst_amount"], row["status"]) == (5000000, 900000, "IN_STOCK")
    assert row["manufacturing_month"] == date.today().strftime("%Y-%m")


def test_save_and_add_another_keeps_shared_fields(qtbot, conn, stock, messages):
    d = VehicleDialog(conn)
    qtbot.addWidget(d)
    d.model.setValue(stock["activa"])
    d.color.setCurrentText("Pearl White")
    d.purchase_invoice.setText("HMSI-778")
    for n in (10, 11):
        d.chassis.setText(f"CH{n}")
        d.engine.setText(f"EN{n}")
        d.save_and_add_another()
    assert messages == []
    assert d.saved_count == 2 and d.chassis.text() == "" and d.color.currentText() == "Pearl White"
    rows = conn.execute("SELECT chassis_number FROM vehicle WHERE purchase_invoice_no = 'HMSI-778'").fetchall()
    assert [r[0] for r in rows] == ["CH10", "CH11"]

    d.chassis.setText("CH1")  # duplicate
    d.engine.setText("EN99")
    d.save_and_add_another()
    assert "already in the system" in messages[-1]
    assert d.chassis.text() == "CH1"  # kept so it can be fixed


def test_sold_vehicle_dialog_is_locked(qtbot, conn, stock):
    sell_vehicle(conn, customer_id=stock["customer"], vehicle_id=stock["v1"])
    d = VehicleDialog(conn, stock["v1"])
    qtbot.addWidget(d)
    assert not d.chassis.isEnabled() and d.registration.isEnabled()
    d.registration.setText("UP80AB1234")
    d.accept()
    assert conn.execute("SELECT registration_number FROM vehicle WHERE id = ?", (stock["v1"],)).fetchone()[0] == "UP80AB1234"


def test_vehicles_page_filters_and_footer(qtbot, conn, stock):
    page = VehiclesPage(conn)
    qtbot.addWidget(page)
    assert page.table.rowCount() == 3
    assert page.footer.text().startswith("3 vehicles shown  ·  3 in stock  ·  stock value ₹80,240.00")
    page.model_filter.setValue(stock["shine"])
    assert page.table.rowCount() == 1
    page.model_filter.setValue(None)
    page.search.setText("ch2")
    assert page.table.rowCount() == 1 and page.table.item(0, 2).text() == "CH2"


def test_table_keeps_given_order_and_sorts_money_numerically(qtbot, conn, stock):
    page = VehiclesPage(conn)
    qtbot.addWidget(page)
    # Same order as find_vehicles (dated purchases first), not re-sorted by the Model column.
    assert [page.table.item(r, 2).text() for r in range(3)] == [r["chassis_number"] for r in find_vehicles(conn)]
    cost_column = [c.title for c in page.table.columns].index("Cost")
    page.table.sortItems(cost_column)
    assert page.table.item(2, 2).text() == "CH1"


def test_sortable_item_compares_keys_not_text(qtbot):
    from jbhonda.ui.widgets import Column, DataTable

    table = DataTable([Column("Amount", lambda r: r["text"], sort_key=lambda r: r["paise"])])
    qtbot.addWidget(table)
    table.fill([{"id": 1, "text": "₹9.00", "paise": 900}, {"id": 2, "text": "₹10.00", "paise": 1000}])
    table.sortItems(0)
    assert [table.item(r, 0).text() for r in range(2)] == ["₹9.00", "₹10.00"]
