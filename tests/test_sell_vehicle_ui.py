import pytest

from jbhonda.core.bookings import allot_vehicle, create_booking
from jbhonda.core.customers import save_customer
from jbhonda.core.inventory import save_part, save_vehicle, save_vehicle_model
from jbhonda.core.masters import save_financer, save_insurer
from jbhonda.core.sales import balance_due
from jbhonda.ui import widgets
from jbhonda.ui.bookings_page import AdvanceDialog, AllotVehicleDialog, BookingsPage, CancelBookingDialog, NewBookingDialog
from jbhonda.ui.customer_widgets import CustomerDialog, CustomerPicker
from jbhonda.ui.main_window import MainWindow
from jbhonda.ui.sell_vehicle_page import SellVehiclePage


@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(widgets, "show_error", lambda parent, msg: shown.append(("error", msg)))
    monkeypatch.setattr(widgets, "show_info", lambda parent, msg: shown.append(("info", msg)))
    monkeypatch.setattr(widgets, "confirm", lambda parent, msg: shown.append(("confirm", msg)) or True)
    return shown


@pytest.fixture
def shop(conn):
    activa = save_vehicle_model(conn, model_name="Activa 6G", category="SCOOTER", gst_rate_bp=1800,
                                ex_showroom_price=8500000)
    return {
        "activa": activa,
        "a1": save_vehicle(conn, vehicle_model_id=activa, chassis_number="A1", engine_number="EA1", color="White"),
        "a2": save_vehicle(conn, vehicle_model_id=activa, chassis_number="A2", engine_number="EA2", color="Grey"),
        "ramesh": save_customer(conn, name="Ramesh Kumar", phone="9876543210", city="Agra"),
        "hdfc": save_financer(conn, name="HDFC Bank"),
        "icici": save_insurer(conn, name="ICICI Lombard"),
        "helmet": save_part(conn, part_number="HLM", name="Helmet", hsn_code="6506", gst_rate_bp=1800,
                            sale_price=150000, opening_qty=2),
    }


def test_customer_picker_search_and_new(qtbot, conn, shop, messages, monkeypatch):
    picker = CustomerPicker(conn)
    qtbot.addWidget(picker)
    seen = []
    picker.changed.connect(seen.append)
    picker.search.setText("98765")
    assert picker.customer_id() == shop["ramesh"]  # single match picked automatically
    assert seen == [shop["ramesh"]]

    dialog = CustomerDialog(conn, initial_text="91234 56789")
    qtbot.addWidget(dialog)
    assert dialog.phone.text() == "91234 56789"  # digits typed in search go to the phone field
    dialog.name.setText("Sita Devi")
    dialog.accept()
    assert messages == []
    assert conn.execute("SELECT phone FROM customer WHERE name = 'Sita Devi'").fetchone()[0] == "9123456789"


def test_simple_cash_sale(qtbot, conn, shop, messages):
    page = SellVehiclePage(conn)
    qtbot.addWidget(page)
    page.customer.set_customer(shop["ramesh"])
    assert "Agra" in page.customer_details.text()
    page.vehicle.setValue(shop["a1"])
    assert page.price.text() == "85000.00"
    page.discount.setText("1000")
    page.payment_rows[0].amount.setText("84000")
    assert page.summary_labels["total"].text() == "₹84,000.00"
    assert page.summary_labels["balance"].text() == "₹0.00"

    invoice_id = page.create_invoice()
    kind, text = messages[-1]
    assert kind == "info" and text.startswith("Invoice JBH/V/") and text.endswith("/0001 created.")
    assert balance_due(conn, invoice_id) == 0
    assert page.customer.customer_id() is None and page.vehicle.value() is None  # form cleared


def test_financed_sale_with_accessory_and_insurance(qtbot, conn, shop, messages):
    page = SellVehiclePage(conn)
    qtbot.addWidget(page)
    page.customer.set_customer(shop["ramesh"])
    page.vehicle.setValue(shop["a2"])
    page.accessories.append((conn.execute("SELECT * FROM part").fetchone(), 1, 150000))
    page._show_accessories()
    page.finance_box.setChecked(True)
    page.financer.setValue(shop["hdfc"])
    page.loan_amount.setText("70000")
    page.payment_rows[0].amount.setText("10000")
    page.add_payment_row()
    page.payment_rows[1].mode.setValue("UPI")
    page.payment_rows[1].amount.setText("6500")
    page.payment_rows[1].reference.setText("UTR77")
    page.insurance_box.setChecked(True)
    page.insurer.setValue(shop["icici"])
    page.policy_number.setText("P-9")
    page.premium.setText("6500")
    assert page.summary_labels["total"].text() == "₹86,500.00"
    assert page.summary_labels["balance"].text() == "₹0.00"

    invoice_id = page.create_invoice()
    assert invoice_id is not None, messages
    inv = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
    assert inv["hypothecated_to"] == "HDFC Bank"
    assert balance_due(conn, invoice_id) == 7000000  # loan not yet received
    assert conn.execute("SELECT policy_number FROM insurance_policy").fetchone()[0] == "P-9"


def test_errors_are_shown_and_nothing_saved(qtbot, conn, shop, messages):
    page = SellVehiclePage(conn)
    qtbot.addWidget(page)
    assert page.create_invoice() is None
    assert messages[-1] == ("error", "Choose the customer, or add a new one.")
    page.customer.set_customer(shop["ramesh"])
    page.vehicle.setValue(shop["a1"])
    page.payment_rows[0].amount.setText("90000")
    assert page.create_invoice() is None
    assert "more than the amount due" in messages[-1][1]
    assert conn.execute("SELECT count(*) FROM invoice").fetchone()[0] == 0


def test_typed_price_survives_list_refresh(qtbot, conn, shop):
    page = SellVehiclePage(conn)
    qtbot.addWidget(page)
    page.vehicle.setValue(shop["a1"])
    page.price.setText("83000")
    page.reload_lists()  # happens whenever the page is shown again
    assert page.price.text() == "83000"


def test_booking_flow_through_screens(qtbot, conn, shop, messages):
    d = NewBookingDialog(conn)
    qtbot.addWidget(d)
    d.customer.set_customer(shop["ramesh"])
    d.color.setCurrentText("Grey")
    assert d.quoted_price.text() == "85000.00"
    d.advance.setText("5000")
    d.accept()
    assert messages == []
    booking_id = d.booking_id

    allot = AllotVehicleDialog(conn, booking_id)
    qtbot.addWidget(allot)
    assert allot.vehicle.value() == shop["a2"]  # wanted colour listed first
    allot.accept()

    window = MainWindow(conn)
    qtbot.addWidget(window)
    bookings = window.page_by_name["Bookings"]
    bookings.refresh()
    bookings.table.selectRow(0)
    bookings.sell()
    sell = window.sell_vehicle
    assert window.pages.currentWidget() is sell
    assert sell.customer.customer_id() == shop["ramesh"]
    assert sell.booking.value() == booking_id and sell.vehicle.value() == shop["a2"]
    assert sell.summary_labels["advance"].text() == "- ₹5,000.00"
    sell.payment_rows[0].amount.setText("80000")
    assert sell.summary_labels["balance"].text() == "₹0.00"
    invoice_id = sell.create_invoice()
    assert balance_due(conn, invoice_id) == 0
    assert conn.execute("SELECT status FROM booking").fetchone()[0] == "CONVERTED"


def test_cancel_and_refund_from_bookings_page(qtbot, conn, shop, messages):
    b = create_booking(conn, customer_id=shop["ramesh"], vehicle_model_id=shop["activa"], advance=300000)
    allot_vehicle(conn, b, shop["a1"])
    page = BookingsPage(conn)
    qtbot.addWidget(page)
    page.table.selectRow(0)
    assert page.sell_button.isEnabled() and not page.refund_button.isEnabled()

    cancel = CancelBookingDialog(conn, b)
    qtbot.addWidget(cancel)
    cancel.reason.setText("changed mind")
    cancel.accept()
    page.status_filter.setValue(None)  # show all
    page.table.selectRow(0)
    assert page.refund_button.isEnabled() and not page.sell_button.isEnabled()

    refund = AdvanceDialog(conn, b, refund=True)
    qtbot.addWidget(refund)
    assert refund.amount.text() == "3000.00"
    refund.accept()
    assert messages == []
    page.refresh()
    assert page.footer.text().endswith("advance held ₹0.00")


def test_reset_leaves_exactly_one_payment_row(qtbot, conn, shop):
    page = SellVehiclePage(conn)
    qtbot.addWidget(page)
    page.add_payment_row()
    page.reset()
    page.reset()
    rows = [page.payments_layout.itemAt(i).widget() for i in range(page.payments_layout.count())]
    assert sum(1 for w in rows if w in page.payment_rows) == 1
    assert page.payments_layout.count() == 2  # the row + the "Add another payment" button
