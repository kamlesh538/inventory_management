from datetime import date

import pytest

from jbhonda.core.customers import save_customer
from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import part_history, save_part, save_vehicle, save_vehicle_model
from jbhonda.core.invoices import find_invoices
from jbhonda.core.sales import PaymentIn, balance_due, cancel_invoice, sell_vehicle
from jbhonda.core.service import (
    add_job_labour, add_job_part, bill_job, cancel_job, create_job, customer_vehicles, find_jobs, job_totals,
    remove_job_labour, remove_job_part, update_job,
)

DAY = date(2026, 10, 24)


@pytest.fixture
def shop(conn):
    model = save_vehicle_model(conn, model_name="Activa 6G", category="SCOOTER", gst_rate_bp=1800,
                               ex_showroom_price=8500000)
    vehicle = save_vehicle(conn, vehicle_model_id=model, chassis_number="ME4CH1", engine_number="EN1", color="Red")
    ramesh = save_customer(conn, name="Ramesh", phone="9876543210")
    return {
        "vehicle": vehicle, "ramesh": ramesh,
        "oil": save_part(conn, part_number="OIL", name="Engine Oil", hsn_code="2710", gst_rate_bp=1800,
                         sale_price=40000, mrp=42000, opening_qty=10, opening_unit_cost=30000),
        "plug": save_part(conn, part_number="PLUG", name="Spark Plug", hsn_code="8511", gst_rate_bp=1800,
                          sale_price=14000, opening_qty=3),
        "general": conn.execute("SELECT id FROM labour_item WHERE name = 'General service'").fetchone()[0],
    }


def _stock(conn, part_id):
    return conn.execute("SELECT stock_qty FROM part WHERE id = ?", (part_id,)).fetchone()[0]


def _job(conn, job_id):
    return conn.execute("SELECT * FROM service_job WHERE id = ?", (job_id,)).fetchone()


def test_full_job_paid_service(conn, shop):
    job = create_job(conn, customer_id=shop["ramesh"], vehicle_description="Shine 100",
                     registration_number="up80 ab 1234", odometer_km=5200, complaints="Brake noise")
    assert _job(conn, job)["job_no"].startswith("JBH/J/")
    add_job_part(conn, job, part_id=shop["oil"], quantity=1)
    plug_line = add_job_part(conn, job, part_id=shop["plug"], quantity=2)
    assert (_stock(conn, shop["oil"]), _stock(conn, shop["plug"])) == (9, 1)  # issued straight away
    remove_job_part(conn, plug_line)
    assert _stock(conn, shop["plug"]) == 3  # back in stock
    add_job_labour(conn, job, labour_item_id=shop["general"])           # ₹350 from the price list
    add_job_labour(conn, job, description="Brake adjustment", amount=10000)
    assert job_totals(conn, job).total == 40000 + 35000 + 10000

    invoice_id = bill_job(conn, job, payments=[PaymentIn("UPI", 85000, "UTR1")], work_done="Serviced",
                          next_service_date=date(2027, 1, 24), next_service_km=8200, on=DAY)
    inv = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
    assert inv["invoice_type"] == "SERVICE" and inv["invoice_no"] == "JBH/S/2026-27/0001"
    assert inv["grand_total"] == 85000 and balance_due(conn, invoice_id) == 0
    kinds = [r[0] for r in conn.execute("SELECT item_kind FROM invoice_item WHERE invoice_id = ? ORDER BY line_no",
                                        (invoice_id,))]
    assert kinds == ["PART", "LABOUR", "LABOUR"]
    labour_sac = conn.execute("SELECT DISTINCT hsn_code FROM invoice_item WHERE item_kind = 'LABOUR'").fetchall()
    assert [r[0] for r in labour_sac] == ["998714"]
    assert _stock(conn, shop["oil"]) == 9  # billing didn't take it from stock a second time
    j = _job(conn, job)
    assert (j["status"], j["registration_number"], j["next_service_km"]) == ("BILLED", "UP80 AB 1234", 8200)
    with pytest.raises(BusinessError, match="billed"):
        add_job_part(conn, job, part_id=shop["oil"], quantity=1)


def test_free_service_labour_free_parts_charged(conn, shop):
    with pytest.raises(BusinessError, match="which free service"):
        create_job(conn, customer_id=shop["ramesh"], vehicle_id=shop["vehicle"], service_type="FREE")
    job = create_job(conn, customer_id=shop["ramesh"], vehicle_id=shop["vehicle"], service_type="FREE",
                     free_service_no=1)
    assert _job(conn, job)["vehicle_description"] == "Activa 6G"  # filled from our record
    add_job_labour(conn, job, labour_item_id=shop["general"], amount=0)
    add_job_part(conn, job, part_id=shop["oil"], quantity=1)
    invoice_id = bill_job(conn, job, payments=[PaymentIn("CASH", 40000)])
    assert conn.execute("SELECT grand_total, notes FROM invoice WHERE id = ?", (invoice_id,)).fetchone()[:] == (
        40000, f"Free service 1. Job card {_job(conn, job)['job_no']}")


def test_cancel_job_returns_all_parts(conn, shop):
    job = create_job(conn, customer_id=shop["ramesh"], vehicle_description="Dio")
    add_job_part(conn, job, part_id=shop["oil"], quantity=2)
    with pytest.raises(BusinessError, match="reason"):
        cancel_job(conn, job, " ")
    cancel_job(conn, job, "customer took vehicle back")
    assert _stock(conn, shop["oil"]) == 10 and _job(conn, job)["status"] == "CANCELLED"
    types = [m["movement_type"] for m in part_history(conn, shop["oil"])]
    assert types == ["PURCHASE", "JOB_ISSUE", "JOB_RETURN"]


def test_cancelling_service_invoice_reopens_job(conn, shop):
    job = create_job(conn, customer_id=shop["ramesh"], vehicle_description="Dio")
    add_job_part(conn, job, part_id=shop["oil"], quantity=1)
    labour = add_job_labour(conn, job, description="Wash", amount=15000)
    invoice_id = bill_job(conn, job)
    cancel_invoice(conn, invoice_id, "wrong labour charge")
    j = _job(conn, job)
    assert (j["status"], j["invoice_id"]) == ("OPEN", None)
    assert _stock(conn, shop["oil"]) == 9  # still issued to the job
    remove_job_labour(conn, labour)
    add_job_labour(conn, job, description="Wash", amount=10000)
    second = bill_job(conn, job)
    assert conn.execute("SELECT grand_total FROM invoice WHERE id = ?", (second,)).fetchone()[0] == 50000


def test_rules(conn, shop):
    job = create_job(conn, customer_id=shop["ramesh"], vehicle_description="Dio")
    with pytest.raises(BusinessError, match="before billing"):
        bill_job(conn, job)
    with pytest.raises(BusinessError, match="Only 3"):
        add_job_part(conn, job, part_id=shop["plug"], quantity=4)
    with pytest.raises(BusinessError, match="MRP"):
        add_job_part(conn, job, part_id=shop["oil"], quantity=1, unit_price=42100)
    add_job_part(conn, job, part_id=shop["plug"], quantity=1)
    with pytest.raises(BusinessError, match="more than the bill"):
        bill_job(conn, job, payments=[PaymentIn("CASH", 14100)])
    assert _job(conn, job)["status"] == "OPEN"  # nothing half-done
    invoice_id = bill_job(conn, job, payments=[PaymentIn("CASH", 4000)])  # credit to a known customer
    assert balance_due(conn, invoice_id) == 10000


def test_update_job_and_free_service_number(conn, shop):
    job = create_job(conn, customer_id=shop["ramesh"], vehicle_description="Dio")
    update_job(conn, job, service_type="FREE", free_service_no=2, odometer_km=3000, mechanic_name="Raju")
    assert (_job(conn, job)["free_service_no"], _job(conn, job)["mechanic_name"]) == (2, "Raju")
    update_job(conn, job, service_type="PAID")
    assert _job(conn, job)["free_service_no"] is None
    with pytest.raises(TypeError):
        update_job(conn, job, status="BILLED")


def test_lookups(conn, shop):
    sell_vehicle(conn, customer_id=shop["ramesh"], vehicle_id=shop["vehicle"])
    create_job(conn, customer_id=shop["ramesh"], vehicle_description="Shine", registration_number="UP80X1")
    vehicles = customer_vehicles(conn, shop["ramesh"])
    assert {v["vehicle_description"] for v in vehicles} == {"Activa 6G", "Shine"}
    assert [j["registration_number"] for j in find_jobs(conn, text="up80x")] == ["UP80X1"]
    assert len(find_jobs(conn, status="OPEN")) == 1
    assert [r["invoice_type"] for r in find_invoices(conn, invoice_type="SERVICE")] == []
