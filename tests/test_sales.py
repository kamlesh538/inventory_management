from datetime import date

import pytest

from jbhonda.core.customers import add_customer
from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import (
    add_part, save_vehicle, save_vehicle_model, adjust_stock, low_stock_parts, purchase_part,
)
from jbhonda.core.numbering import next_number
from jbhonda.core.sales import (
    PartLine, amount_paid, balance_due, cancel_invoice, record_payment, sell_parts, sell_vehicle,
)
from jbhonda.db import transaction

DAY = date(2026, 10, 24)


@pytest.fixture
def shop(conn):
    """A shop with one Activa in stock, a customer, and two parts."""
    model = save_vehicle_model(
        conn, model_name="Activa 6G", variant="DLX", category="SCOOTER", engine_cc=110,
        gst_rate_bp=1800, ex_showroom_price=8500000,
    )
    vehicle = save_vehicle(
        conn, vehicle_model_id=model, chassis_number=" me4jf91aapg000001 ", engine_number="jf91eg0000001",
        color="Pearl White", purchase_taxable_value=6800000, purchase_gst_amount=1224000,
    )
    customer = add_customer(
        conn, name="Ramesh Kumar", phone="9876543210", relation_type="S/O", relation_name="Suresh Kumar",
        address_line1="12 Station Road", city="Agra", state="Uttar Pradesh", pincode="282001",
    )
    helmet = add_part(
        conn, part_number="HLM-001", name="Helmet", category="ACCESSORY", hsn_code="6506",
        gst_rate_bp=1800, sale_price=150000, opening_qty=5, opening_unit_cost=100000, reorder_level=2,
    )
    filt = add_part(
        conn, part_number="17210-K0N-D00", name="Air Filter", hsn_code="8714", gst_rate_bp=1800,
        sale_price=35000, opening_qty=10, opening_unit_cost=25000,
    )
    return {"model": model, "vehicle": vehicle, "customer": customer, "helmet": helmet, "filter": filt}


def _stock(conn, part_id):
    return conn.execute("SELECT stock_qty FROM part WHERE id = ?", (part_id,)).fetchone()[0]


def _vehicle_status(conn, vehicle_id):
    return conn.execute("SELECT status FROM vehicle WHERE id = ?", (vehicle_id,)).fetchone()[0]


# ---------------------------------------------------------------- numbering

def test_numbers_are_sequential_per_series_and_reset_each_financial_year(conn):
    with transaction(conn):
        assert next_number(conn, "VEHICLE", date(2027, 3, 31)) == "JBH/V/2026-27/0001"
        assert next_number(conn, "VEHICLE", date(2027, 3, 31)) == "JBH/V/2026-27/0002"
        assert next_number(conn, "PART", date(2027, 3, 31)) == "JBH/P/2026-27/0001"
        assert next_number(conn, "VEHICLE", date(2027, 4, 1)) == "JBH/V/2027-28/0001"


def test_failed_sale_does_not_use_up_an_invoice_number(conn, shop):
    with pytest.raises(BusinessError):
        sell_parts(conn, lines=[PartLine(shop["filter"], 999)], on=DAY)
    invoice_id = sell_parts(conn, lines=[PartLine(shop["filter"], 1)], on=DAY)
    assert conn.execute("SELECT invoice_no FROM invoice WHERE id = ?", (invoice_id,)).fetchone()[0] == "JBH/P/2026-27/0001"


# ---------------------------------------------------------------- inventory

def test_chassis_and_engine_numbers_are_normalised_and_unique(conn, shop):
    row = conn.execute("SELECT chassis_number, engine_number FROM vehicle").fetchone()
    assert tuple(row) == ("ME4JF91AAPG000001", "JF91EG0000001")
    with pytest.raises(BusinessError, match="chassis number ME4JF91AAPG000001"):
        save_vehicle(conn, vehicle_model_id=shop["model"], chassis_number="ME4JF91AAPG000001",
                    engine_number="NEW", color="Red")


def test_purchase_updates_weighted_average_cost(conn, shop):
    # 10 in stock at ₹250, buy 10 more at ₹300 -> average ₹275
    purchase_part(conn, part_id=shop["filter"], quantity=10, unit_cost=30000, reference_no="HMSI-123")
    row = conn.execute("SELECT stock_qty, avg_cost FROM part WHERE id = ?", (shop["filter"],)).fetchone()
    assert tuple(row) == (20, 27500)


def test_adjustment_needs_reason_and_cannot_go_negative(conn, shop):
    with pytest.raises(BusinessError, match="reason"):
        adjust_stock(conn, part_id=shop["filter"], quantity_change=-1, reason=" ")
    with pytest.raises(BusinessError, match="Only 10"):
        adjust_stock(conn, part_id=shop["filter"], quantity_change=-11, reason="count")
    adjust_stock(conn, part_id=shop["filter"], quantity_change=-2, reason="damaged in rain")
    assert _stock(conn, shop["filter"]) == 8


def test_low_stock_list(conn, shop):
    assert low_stock_parts(conn) == []
    sell_parts(conn, lines=[PartLine(shop["helmet"], 3)], on=DAY)
    assert [p["part_number"] for p in low_stock_parts(conn)] == ["HLM-001"]


# ---------------------------------------------------------------- selling a vehicle

def test_sell_vehicle_with_accessory(conn, shop):
    invoice_id = sell_vehicle(
        conn, customer_id=shop["customer"], vehicle_id=shop["vehicle"], discount=100000,
        accessories=[PartLine(shop["helmet"], 1)], on=DAY, hypothecated_to="HDFC Bank",
    )
    inv = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
    assert inv["invoice_no"] == "JBH/V/2026-27/0001"
    assert inv["bill_name"] == "Ramesh Kumar"
    assert inv["bill_relation"] == "S/O Suresh Kumar"
    assert inv["bill_address"] == "12 Station Road, Agra, Uttar Pradesh, 282001"
    assert inv["hypothecated_to"] == "HDFC Bank"
    # ₹85,000 - ₹1,000 discount + ₹1,500 helmet
    assert inv["grand_total"] == 8550000
    assert inv["total_taxable"] + inv["total_cgst"] + inv["total_sgst"] + inv["round_off"] == inv["grand_total"]
    assert inv["total_discount"] == 100000

    items = conn.execute("SELECT * FROM invoice_item WHERE invoice_id = ? ORDER BY line_no", (invoice_id,)).fetchall()
    assert [i["item_kind"] for i in items] == ["VEHICLE", "PART"]
    assert "Chassis No: ME4JF91AAPG000001" in items[0]["description"]
    assert items[0]["unit_cost"] == 6800000
    assert _vehicle_status(conn, shop["vehicle"]) == "SOLD"
    assert _stock(conn, shop["helmet"]) == 4


def test_vehicle_cannot_be_sold_twice(conn, shop):
    sell_vehicle(conn, customer_id=shop["customer"], vehicle_id=shop["vehicle"], on=DAY)
    with pytest.raises(BusinessError, match="not available"):
        sell_vehicle(conn, customer_id=shop["customer"], vehicle_id=shop["vehicle"], on=DAY)


def test_invoice_keeps_customer_details_after_customer_is_edited(conn, shop):
    invoice_id = sell_vehicle(conn, customer_id=shop["customer"], vehicle_id=shop["vehicle"], on=DAY)
    conn.execute("UPDATE customer SET name = 'Someone Else'")
    assert conn.execute("SELECT bill_name FROM invoice WHERE id = ?", (invoice_id,)).fetchone()[0] == "Ramesh Kumar"


def test_discount_larger_than_price_is_rejected(conn, shop):
    with pytest.raises(BusinessError, match="Discount"):
        sell_vehicle(conn, customer_id=shop["customer"], vehicle_id=shop["vehicle"], discount=9000000, on=DAY)
    assert _vehicle_status(conn, shop["vehicle"]) == "IN_STOCK"


# ---------------------------------------------------------------- selling parts

def test_walk_in_part_sale(conn, shop):
    invoice_id = sell_parts(
        conn, lines=[PartLine(shop["filter"], 2), PartLine(shop["helmet"], 1, unit_price=140000)], on=DAY,
    )
    inv = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice_id,)).fetchone()
    assert inv["bill_name"] == "Cash Customer"
    assert inv["customer_id"] is None
    assert inv["grand_total"] == 2 * 35000 + 140000
    assert _stock(conn, shop["filter"]) == 8
    assert _stock(conn, shop["helmet"]) == 4


def test_stock_check_counts_repeated_lines_together(conn, shop):
    with pytest.raises(BusinessError, match="Only 5 of HLM-001"):
        sell_parts(conn, lines=[PartLine(shop["helmet"], 3), PartLine(shop["helmet"], 3)], on=DAY)
    assert _stock(conn, shop["helmet"]) == 5


def test_empty_bill_rejected(conn):
    with pytest.raises(BusinessError):
        sell_parts(conn, lines=[], on=DAY)


# ---------------------------------------------------------------- cancelling

def test_cancel_restores_vehicle_and_parts(conn, shop):
    invoice_id = sell_vehicle(
        conn, customer_id=shop["customer"], vehicle_id=shop["vehicle"],
        accessories=[PartLine(shop["helmet"], 2)], on=DAY,
    )
    cancel_invoice(conn, invoice_id, "customer changed mind", on=DAY)

    assert conn.execute("SELECT status FROM invoice WHERE id = ?", (invoice_id,)).fetchone()[0] == "CANCELLED"
    assert _vehicle_status(conn, shop["vehicle"]) == "IN_STOCK"
    assert _stock(conn, shop["helmet"]) == 5
    with pytest.raises(BusinessError, match="already cancelled"):
        cancel_invoice(conn, invoice_id, "again")
    # The vehicle can now be sold again, under a new invoice number.
    again = sell_vehicle(conn, customer_id=shop["customer"], vehicle_id=shop["vehicle"], on=DAY)
    assert conn.execute("SELECT invoice_no FROM invoice WHERE id = ?", (again,)).fetchone()[0] == "JBH/V/2026-27/0002"


def test_cancel_needs_reason(conn, shop):
    invoice_id = sell_parts(conn, lines=[PartLine(shop["filter"], 1)], on=DAY)
    with pytest.raises(BusinessError, match="reason"):
        cancel_invoice(conn, invoice_id, "")


# ---------------------------------------------------------------- payments

def test_split_payment_and_balance(conn, shop):
    invoice_id = sell_vehicle(conn, customer_id=shop["customer"], vehicle_id=shop["vehicle"], on=DAY)
    receipt = record_payment(conn, invoice_id=invoice_id, amount=2000000, mode="CASH", payment_type="DOWN_PAYMENT", on=DAY)
    assert receipt == "JBH/R/2026-27/0001"
    record_payment(conn, invoice_id=invoice_id, amount=6500000, mode="UPI", reference_no="UTR123",
                   payment_type="BALANCE", on=DAY)
    assert amount_paid(conn, invoice_id) == 8500000
    assert balance_due(conn, invoice_id) == 0


def test_overpayment_rejected(conn, shop):
    invoice_id = sell_parts(conn, lines=[PartLine(shop["filter"], 1)], on=DAY)
    with pytest.raises(BusinessError, match="more than the balance"):
        record_payment(conn, invoice_id=invoice_id, amount=35100, mode="CASH")


def test_digital_payment_needs_reference(conn, shop):
    invoice_id = sell_parts(conn, lines=[PartLine(shop["filter"], 1)], on=DAY)
    with pytest.raises(BusinessError, match="reference"):
        record_payment(conn, invoice_id=invoice_id, amount=35000, mode="UPI")


def test_cancelled_invoice_shows_refund_due_and_accepts_only_refunds(conn, shop):
    invoice_id = sell_parts(conn, lines=[PartLine(shop["filter"], 1)], on=DAY)
    record_payment(conn, invoice_id=invoice_id, amount=35000, mode="CASH", on=DAY)
    cancel_invoice(conn, invoice_id, "wrong part", on=DAY)

    assert balance_due(conn, invoice_id) == -35000
    with pytest.raises(BusinessError, match="only receive refunds"):
        record_payment(conn, invoice_id=invoice_id, amount=100, mode="CASH")
    with pytest.raises(BusinessError, match="more than the amount overpaid"):
        record_payment(conn, invoice_id=invoice_id, amount=35001, mode="CASH", payment_type="REFUND")
    record_payment(conn, invoice_id=invoice_id, amount=35000, mode="CASH", payment_type="REFUND", on=DAY)
    assert balance_due(conn, invoice_id) == 0
