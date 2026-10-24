import pytest

from jbhonda.core.customers import save_customer
from jbhonda.core.inventory import save_part, save_vehicle, save_vehicle_model
from jbhonda.core.sales import balance_due, sell_vehicle
from jbhonda.core.service import create_job
from jbhonda.ui import widgets
from jbhonda.ui.service_page import BillJobDialog, CancelJobDialog, JobCardDialog, LabourDialog, NewJobDialog, ServicePage


@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(widgets, "show_error", lambda parent, msg: shown.append(("error", msg)))
    monkeypatch.setattr(widgets, "show_info", lambda parent, msg: shown.append(("info", msg)))
    monkeypatch.setattr(widgets, "confirm", lambda parent, msg: True)
    return shown


@pytest.fixture
def shop(conn):
    model = save_vehicle_model(conn, model_name="Activa 6G", category="SCOOTER", gst_rate_bp=1800, ex_showroom_price=1)
    vehicle = save_vehicle(conn, vehicle_model_id=model, chassis_number="ME4CH1", engine_number="E1", color="Red")
    customer = save_customer(conn, name="Ramesh Kumar", phone="9876543210")
    sell_vehicle(conn, customer_id=customer, vehicle_id=vehicle)
    oil = save_part(conn, part_number="OIL", name="Engine Oil", hsn_code="2710", gst_rate_bp=1800, sale_price=40000,
                    opening_qty=5)
    return {"customer": customer, "vehicle": vehicle, "oil": oil}


def test_new_job_prefills_customers_vehicle(qtbot, conn, shop, messages):
    d = NewJobDialog(conn)
    qtbot.addWidget(d)
    d.customer.set_customer(shop["customer"])
    assert d.header.vehicle_description.text() == "Activa 6G" and d.header.chassis.text() == "ME4CH1"
    d.header.registration.setText("up80ab1234")
    d.header.odometer.setValue(5100)
    d.header.service_type.setValue("FREE")
    d.header.free_no.setValue(2)
    d.accept()
    assert messages == []
    job = conn.execute("SELECT * FROM service_job").fetchone()
    assert (job["vehicle_id"], job["registration_number"], job["free_service_no"]) == (shop["vehicle"], "UP80AB1234", 2)


def test_job_card_parts_labour_and_billing(qtbot, conn, shop, messages, monkeypatch):
    job_id = create_job(conn, customer_id=shop["customer"], vehicle_description="Activa 6G", odometer_km=5000)
    card = JobCardDialog(conn, job_id)
    qtbot.addWidget(card)

    from jbhonda.ui import service_page
    def pick_part(self):
        self.result_line = (conn.execute("SELECT * FROM part").fetchone(), 2, 40000)
        return True

    monkeypatch.setattr(service_page.AccessoryDialog, "exec", pick_part)
    card.add_part()
    assert card.parts_table.rowCount() == 1
    assert conn.execute("SELECT stock_qty FROM part").fetchone()[0] == 3

    labour = LabourDialog(conn, free_service=False)
    qtbot.addWidget(labour)
    assert labour.description.text() and labour.amount.value() is not None  # first price-list item preselected
    labour.accept()
    def pick_labour(self):
        self.result_values = {"labour_item_id": None, "description": "Wash", "amount": 10000}
        return True

    monkeypatch.setattr(service_page.LabourDialog, "exec", pick_labour)
    card.add_labour()
    assert card.totals.text().endswith("=  ₹900.00")

    card.parts_table.selectRow(0)
    card.remove_part()
    assert conn.execute("SELECT stock_qty FROM part").fetchone()[0] == 5

    bill = BillJobDialog(conn, job_id)
    qtbot.addWidget(bill)
    assert bill.payments[0].amount.text() == "100.00" and bill.next_km.value() == 8000
    bill.accept()
    assert messages == [] and balance_due(conn, bill.invoice_id) == 0
    assert conn.execute("SELECT status FROM service_job").fetchone()[0] == "BILLED"


def test_free_service_labour_defaults_to_zero(qtbot, conn):
    d = LabourDialog(conn, free_service=True)
    qtbot.addWidget(d)
    assert d.amount.value() == 0


def test_service_page_actions(qtbot, conn, shop, messages, printed_files):
    job_id = create_job(conn, customer_id=shop["customer"], vehicle_description="Activa 6G")
    page = ServicePage(conn)
    qtbot.addWidget(page)
    assert page.table.rowCount() == 1 and page.footer.text().startswith("1 vehicles in the workshop")
    page.table.selectRow(0)
    assert page.bill_button.isEnabled() and not page.print_button.isEnabled()

    cancel = CancelJobDialog(conn, job_id)
    qtbot.addWidget(cancel)
    cancel.reason.setText("customer left")
    cancel.accept()
    page.refresh()
    assert page.table.rowCount() == 0  # default view: vehicles in the workshop
    page.status_filter.setValue(None)
    assert page.table.rowCount() == 1
