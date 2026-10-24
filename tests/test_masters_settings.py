from datetime import date

import pytest

from jbhonda.core.dashboard import summary
from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import save_part, save_vehicle, save_vehicle_model
from jbhonda.core.masters import save_financer, save_insurer, set_active
from jbhonda.core.sales import PartLine, sell_parts
from jbhonda.core.settings import get_settings, update_settings
from jbhonda.core.validation import is_valid_gstin, is_valid_mobile, is_valid_pincode

GSTIN = "27AAPFU0939F1ZV"


@pytest.mark.parametrize(("gstin", "ok"), [
    (GSTIN, True), ("29AAGCB7383J1Z4", True), (" 27aapfu0939f1zv ", True),
    ("27AAPFU0939F1ZX", False), ("27AAPFU0939F1Z", False), ("", False),
])
def test_gstin(gstin, ok):
    assert is_valid_gstin(gstin) is ok


@pytest.mark.parametrize(("phone", "ok"), [
    ("9876543210", True), ("+91 98765 43210", True), ("09876543210", True),
    ("1234567890", False), ("98765", False),
])
def test_mobile(phone, ok):
    assert is_valid_mobile(phone) is ok


def test_pincode():
    assert is_valid_pincode("282001") and not is_valid_pincode("082001") and not is_valid_pincode("2820")


# ---------------------------------------------------------------- settings

def test_settings_gstin_fills_state_code_and_pan(conn):
    update_settings(conn, gstin=GSTIN.lower(), city=" Agra ")
    s = get_settings(conn)
    assert (s["gstin"], s["state_code"], s["pan"], s["city"]) == (GSTIN, "27", "AAPFU0939F", "Agra")
    update_settings(conn, gstin="")
    assert get_settings(conn)["state_code"] is None


def test_settings_reject_bad_values(conn):
    with pytest.raises(BusinessError, match="GSTIN"):
        update_settings(conn, gstin="27AAPFU0939F1ZX")
    with pytest.raises(BusinessError, match="Shop name"):
        update_settings(conn, shop_name="  ")
    with pytest.raises(BusinessError, match="PIN"):
        update_settings(conn, pincode="12")
    with pytest.raises(TypeError):
        update_settings(conn, id=5)


# ---------------------------------------------------------------- masters

def test_vehicle_model_edit_and_duplicate_check(conn):
    a = save_vehicle_model(conn, model_name="Shine", variant="Drum", category="MOTORCYCLE", gst_rate_bp=1800,
                           ex_showroom_price=8000000)
    save_vehicle_model(conn, model_name="Shine", variant="Disc", category="MOTORCYCLE", gst_rate_bp=1800,
                       ex_showroom_price=8500000)
    with pytest.raises(BusinessError, match="already exists"):
        save_vehicle_model(conn, model_id=a, model_name="shine", variant="disc", category="MOTORCYCLE",
                           gst_rate_bp=1800, ex_showroom_price=1)
    save_vehicle_model(conn, model_id=a, model_name="Shine", variant="Drum", category="MOTORCYCLE",
                       gst_rate_bp=1800, ex_showroom_price=8200000)
    assert conn.execute("SELECT ex_showroom_price FROM vehicle_model WHERE id = ?", (a,)).fetchone()[0] == 8200000


def test_inactive_model_cannot_take_new_stock(conn):
    m = save_vehicle_model(conn, model_name="Dio", category="SCOOTER", gst_rate_bp=1800, ex_showroom_price=1)
    set_active(conn, "vehicle_model", m, False)
    with pytest.raises(BusinessError, match="no longer active"):
        save_vehicle(conn, vehicle_model_id=m, chassis_number="C1", engine_number="E1", color="Red")


def test_financer_and_insurer(conn):
    f = save_financer(conn, name="HDFC Bank", branch="Agra")
    save_financer(conn, name="HDFC Bank", branch="Mathura")
    with pytest.raises(BusinessError, match="already in the list"):
        save_financer(conn, name="hdfc bank", branch="agra")
    save_financer(conn, financer_id=f, name="HDFC Bank", branch="Agra", phone=" 9876543210 ")
    assert conn.execute("SELECT phone FROM financer WHERE id = ?", (f,)).fetchone()[0] == "9876543210"

    i = save_insurer(conn, name="ICICI Lombard")
    with pytest.raises(BusinessError):
        save_insurer(conn, name="icici lombard")
    set_active(conn, "insurer", i, False)
    assert conn.execute("SELECT is_active FROM insurer").fetchone()[0] == 0
    with pytest.raises(ValueError):
        set_active(conn, "invoice", 1, False)


def test_dashboard_summary(conn):
    m = save_vehicle_model(conn, model_name="Dio", category="SCOOTER", gst_rate_bp=1800, ex_showroom_price=1)
    save_vehicle(conn, vehicle_model_id=m, chassis_number="C1", engine_number="E1", color="Red")
    p = save_part(conn, part_number="P1", name="Plug", hsn_code="8511", gst_rate_bp=1800, sale_price=10000,
                 opening_qty=3, reorder_level=2)
    sell_parts(conn, lines=[PartLine(p, 1)], on=date(2026, 10, 20))
    sell_parts(conn, lines=[PartLine(p, 1)], on=date(2026, 10, 24))
    s = summary(conn, today=date(2026, 10, 24))
    assert (s.vehicles_in_stock, s.low_stock_parts, s.sales_today, s.sales_this_month) == (1, 1, 10000, 20000)
