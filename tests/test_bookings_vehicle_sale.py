from datetime import date

import pytest

from jbhonda.core.bookings import (
    add_advance, advance_held, allot_vehicle, cancel_booking, create_booking, find_bookings, refund_advance,
)
from jbhonda.core.customers import save_customer, search_customers
from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import save_part, save_vehicle, save_vehicle_model
from jbhonda.core.masters import save_financer, save_insurer
from jbhonda.core.sales import PartLine, amount_paid, balance_due, cancel_invoice, sell_vehicle
from jbhonda.core.vehicle_sale import (
    InsuranceIn, LoanIn, PaymentIn, complete_vehicle_sale, record_loan_disbursement,
)

DAY = date(2026, 10, 24)


@pytest.fixture
def shop(conn):
    activa = save_vehicle_model(conn, model_name="Activa 6G", category="SCOOTER", gst_rate_bp=1800,
                                ex_showroom_price=8500000)
    shine = save_vehicle_model(conn, model_name="Shine", category="MOTORCYCLE", gst_rate_bp=1800,
                               ex_showroom_price=7000000)
    ids = {
        "activa": activa, "shine": shine,
        "a1": save_vehicle(conn, vehicle_model_id=activa, chassis_number="A1", engine_number="EA1", color="White"),
        "a2": save_vehicle(conn, vehicle_model_id=activa, chassis_number="A2", engine_number="EA2", color="Grey"),
        "s1": save_vehicle(conn, vehicle_model_id=shine, chassis_number="S1", engine_number="ES1", color="Black"),
        "ramesh": save_customer(conn, name="Ramesh  Kumar", phone="+91 98765-43210"),
        "sita": save_customer(conn, name="Sita Devi", phone="9123456789"),
        "hdfc": save_financer(conn, name="HDFC Bank", branch="Agra"),
        "icici": save_insurer(conn, name="ICICI Lombard"),
        "helmet": save_part(conn, part_number="HLM", name="Helmet", hsn_code="6506", gst_rate_bp=1800,
                            sale_price=150000, opening_qty=3),
    }
    return ids


def _status(conn, table, row_id):
    return conn.execute(f"SELECT status FROM {table} WHERE id = ?", (row_id,)).fetchone()[0]


# ---------------------------------------------------------------- customers

def test_customer_phone_normalised_and_validated(conn, shop):
    row = conn.execute("SELECT name, phone FROM customer WHERE id = ?", (shop["ramesh"],)).fetchone()
    assert tuple(row) == ("Ramesh Kumar", "9876543210")
    with pytest.raises(BusinessError, match="mobile"):
        save_customer(conn, name="X", phone="12345")
    with pytest.raises(BusinessError, match="PIN"):
        save_customer(conn, name="X", phone="9876543211", pincode="12")
    save_customer(conn, customer_id=shop["ramesh"], name="Ramesh Kumar", phone="9876543210", city="Agra",
                  relation_type="S/O", relation_name="Suresh")
    assert conn.execute("SELECT city FROM customer WHERE id = ?", (shop["ramesh"],)).fetchone()[0] == "Agra"
    assert [c["name"] for c in search_customers(conn, "98765")] == ["Ramesh Kumar"]
    assert [c["name"] for c in search_customers(conn, "sita")] == ["Sita Devi"]


# ---------------------------------------------------------------- bookings

def test_booking_lifecycle_to_sale(conn, shop):
    b = create_booking(conn, customer_id=shop["ramesh"], vehicle_model_id=shop["activa"], preferred_color="White",
                       quoted_price=8500000, advance=500000, advance_mode="CASH", on=DAY)
    add_advance(conn, b, amount=200000, mode="UPI", reference_no="UTR1", on=DAY)
    assert advance_held(conn, b) == 700000
    booking = find_bookings(conn, status="ACTIVE")[0]
    assert (booking["booking_no"], booking["advance_held"], booking["status"]) == ("JBH/B/2026-27/0001", 700000, "OPEN")

    allot_vehicle(conn, b, shop["a1"])
    assert _status(conn, "vehicle", shop["a1"]) == "BOOKED"
    allot_vehicle(conn, b, shop["a2"])  # change of mind: A1 is released
    assert (_status(conn, "vehicle", shop["a1"]), _status(conn, "vehicle", shop["a2"])) == ("IN_STOCK", "BOOKED")

    with pytest.raises(BusinessError, match="allotted to booking JBH/B/2026-27/0001"):
        sell_vehicle(conn, customer_id=shop["sita"], vehicle_id=shop["a2"])
    with pytest.raises(BusinessError, match="different customer"):
        sell_vehicle(conn, customer_id=shop["sita"], vehicle_id=shop["a2"], booking_id=b)

    invoice = complete_vehicle_sale(conn, customer_id=shop["ramesh"], vehicle_id=shop["a2"], booking_id=b,
                                    payments=[PaymentIn("CASH", 7800000)], on=DAY)
    assert balance_due(conn, invoice) == 0  # 7,000 advance + 78,000 cash = 85,000
    assert _status(conn, "booking", b) == "CONVERTED"
    assert advance_held(conn, b) == 0
    assert _status(conn, "vehicle", shop["a2"]) == "SOLD"


def test_cancelling_sale_reopens_booking_with_advance(conn, shop):
    b = create_booking(conn, customer_id=shop["ramesh"], vehicle_model_id=shop["activa"], advance=500000, on=DAY)
    allot_vehicle(conn, b, shop["a1"])
    invoice = complete_vehicle_sale(conn, customer_id=shop["ramesh"], vehicle_id=shop["a1"], booking_id=b,
                                    payments=[PaymentIn("CASH", 1000000)], on=DAY)
    cancel_invoice(conn, invoice, "wrong vehicle")
    assert _status(conn, "booking", b) == "OPEN"
    assert advance_held(conn, b) == 500000           # advance back with the booking
    assert amount_paid(conn, invoice) == 1000000     # cash stays on the invoice, to be refunded there
    assert _status(conn, "vehicle", shop["a1"]) == "IN_STOCK"


def test_sale_can_use_different_vehicle_than_allotted(conn, shop):
    b = create_booking(conn, customer_id=shop["ramesh"], vehicle_model_id=shop["activa"], advance=100000, on=DAY)
    allot_vehicle(conn, b, shop["a1"])
    complete_vehicle_sale(conn, customer_id=shop["ramesh"], vehicle_id=shop["s1"], booking_id=b, on=DAY)
    assert _status(conn, "vehicle", shop["a1"]) == "IN_STOCK"
    assert _status(conn, "vehicle", shop["s1"]) == "SOLD"


def test_allot_rules(conn, shop):
    b = create_booking(conn, customer_id=shop["ramesh"], vehicle_model_id=shop["activa"], on=DAY)
    with pytest.raises(BusinessError, match="different model"):
        allot_vehicle(conn, b, shop["s1"])
    sell_vehicle(conn, customer_id=shop["sita"], vehicle_id=shop["a1"])
    with pytest.raises(BusinessError, match="not in stock"):
        allot_vehicle(conn, b, shop["a1"])


def test_cancel_booking_then_refund(conn, shop):
    b = create_booking(conn, customer_id=shop["ramesh"], vehicle_model_id=shop["activa"], advance=500000,
                       advance_mode="UPI", reference_no="UTR9", on=DAY)
    allot_vehicle(conn, b, shop["a1"])
    with pytest.raises(BusinessError, match="Cancel the booking"):
        refund_advance(conn, b, amount=500000, mode="CASH")
    cancel_booking(conn, b, "customer bought elsewhere")
    assert _status(conn, "vehicle", shop["a1"]) == "IN_STOCK"
    with pytest.raises(BusinessError, match="more than the advance"):
        refund_advance(conn, b, amount=500001, mode="CASH")
    refund_advance(conn, b, amount=500000, mode="CASH")
    assert advance_held(conn, b) == 0
    with pytest.raises(BusinessError, match="cancelled"):
        add_advance(conn, b, amount=100, mode="CASH")


def test_booking_validation(conn, shop):
    with pytest.raises(BusinessError, match="reference"):
        create_booking(conn, customer_id=shop["ramesh"], vehicle_model_id=shop["activa"], advance=100, advance_mode="UPI")
    with pytest.raises(BusinessError, match="more than the quoted"):
        create_booking(conn, customer_id=shop["ramesh"], vehicle_model_id=shop["activa"], quoted_price=100, advance=200)
    # A failed booking must not use up a booking number
    b = create_booking(conn, customer_id=shop["ramesh"], vehicle_model_id=shop["activa"], on=DAY)
    assert find_bookings(conn)[0]["booking_no"] == "JBH/B/2026-27/0001"
    assert b


# ---------------------------------------------------------------- complete sale

def test_financed_sale_with_insurance_and_accessory(conn, shop):
    invoice = complete_vehicle_sale(
        conn, customer_id=shop["ramesh"], vehicle_id=shop["a1"], discount=100000,
        accessories=[PartLine(shop["helmet"], 1)],
        payments=[PaymentIn("CASH", 1000000), PaymentIn("UPI", 550000, "UTR5")],
        loan=LoanIn(financer_id=shop["hdfc"], loan_amount=7000000, tenure_months=24, emi_amount=340000),
        insurance=InsuranceIn(insurer_id=shop["icici"], policy_number="pol-1", policy_type="BUNDLED_1OD_5TP",
                              premium=650000, start_date=DAY, end_date=date(2027, 10, 23), nominee_name="Sita"),
        on=DAY,
    )
    inv = conn.execute("SELECT * FROM invoice WHERE id = ?", (invoice,)).fetchone()
    assert inv["grand_total"] == 8550000 and inv["hypothecated_to"] == "HDFC Bank Agra"
    loan = conn.execute("SELECT * FROM loan").fetchone()
    assert (loan["status"], loan["down_payment"], loan["loan_amount"]) == ("SANCTIONED", 1550000, 7000000)
    assert balance_due(conn, invoice) == 7000000  # waiting for the bank
    types = [r[0] for r in conn.execute("SELECT payment_type FROM payment ORDER BY id")]
    assert types == ["DOWN_PAYMENT", "DOWN_PAYMENT"]

    receipt = record_loan_disbursement(conn, loan["id"], on=DAY, reference_no="NEFT123")
    assert receipt == "JBH/R/2026-27/0003"
    assert balance_due(conn, invoice) == 0
    assert _status(conn, "loan", loan["id"]) == "DISBURSED"
    with pytest.raises(BusinessError, match="already"):
        record_loan_disbursement(conn, loan["id"])
    policy = conn.execute("SELECT * FROM insurance_policy").fetchone()
    assert (policy["policy_number"], policy["vehicle_id"]) == ("POL-1", shop["a1"])


def test_overpayment_rolls_back_whole_sale(conn, shop):
    with pytest.raises(BusinessError, match="more than the amount due"):
        complete_vehicle_sale(
            conn, customer_id=shop["ramesh"], vehicle_id=shop["a1"],
            payments=[PaymentIn("CASH", 2000000)],
            loan=LoanIn(financer_id=shop["hdfc"], loan_amount=7000000),
        )
    assert _status(conn, "vehicle", shop["a1"]) == "IN_STOCK"
    assert conn.execute("SELECT count(*) FROM invoice").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM invoice_sequence WHERE last_number > 0").fetchone()[0] == 0


def test_bad_insurance_rolls_back_whole_sale(conn, shop):
    with pytest.raises(BusinessError, match="end date"):
        complete_vehicle_sale(
            conn, customer_id=shop["ramesh"], vehicle_id=shop["a1"], payments=[PaymentIn("CASH", 8500000)],
            insurance=InsuranceIn(insurer_id=shop["icici"], policy_number="P", policy_type="COMPREHENSIVE",
                                  premium=1, start_date=DAY, end_date=date(2026, 1, 1)),
        )
    assert conn.execute("SELECT count(*) FROM payment").fetchone()[0] == 0
    assert _status(conn, "vehicle", shop["a1"]) == "IN_STOCK"


def test_full_payment_and_disbursed_loan(conn, shop):
    paid = complete_vehicle_sale(conn, customer_id=shop["sita"], vehicle_id=shop["s1"],
                                 payments=[PaymentIn("CASH", 7000000)], on=DAY)
    assert conn.execute("SELECT payment_type FROM payment WHERE invoice_id = ?", (paid,)).fetchone()[0] == "FULL_PAYMENT"

    financed = complete_vehicle_sale(
        conn, customer_id=shop["ramesh"], vehicle_id=shop["a1"], payments=[PaymentIn("CASH", 1500000)],
        loan=LoanIn(financer_id=shop["hdfc"], loan_amount=7000000, disbursed=True), on=DAY,
    )
    assert balance_due(conn, financed) == 0
