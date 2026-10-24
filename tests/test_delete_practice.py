import pytest

from jbhonda import config
from jbhonda.core.customers import save_customer
from jbhonda.core.deletion import delete_record
from jbhonda.core.errors import BusinessError
from jbhonda.core.inventory import adjust_stock, purchase_part, save_part, save_vehicle, save_vehicle_model
from jbhonda.core.masters import save_financer
from jbhonda.core.sales import PartLine, sell_parts, sell_vehicle
from jbhonda.db import open_database
from jbhonda.ui import app as app_module
from jbhonda.ui import widgets
from jbhonda.ui.main_window import MainWindow
from jbhonda.ui.masters_page import CustomersTab
from jbhonda.ui.settings_page import SettingsPage


@pytest.fixture
def stock(conn):
    model = save_vehicle_model(conn, model_name="Activa", category="SCOOTER", gst_rate_bp=1800, ex_showroom_price=1)
    return {
        "model": model,
        "v1": save_vehicle(conn, vehicle_model_id=model, chassis_number="C1", engine_number="E1", color="Red"),
        "v2": save_vehicle(conn, vehicle_model_id=model, chassis_number="C2", engine_number="E2", color="Red"),
        "part": save_part(conn, part_number="P1", name="Plug", hsn_code="8511", gst_rate_bp=1800, sale_price=100,
                          opening_qty=5),
        "customer": save_customer(conn, name="Ram", phone="9876543210"),
    }


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_unused_records_can_be_deleted(conn, stock):
    purchase_part(conn, part_id=stock["part"], quantity=3, unit_cost=50)
    adjust_stock(conn, part_id=stock["part"], quantity_change=-1, reason="count")
    delete_record(conn, "part", stock["part"])  # with its stock-in history
    delete_record(conn, "vehicle", stock["v1"])
    delete_record(conn, "customer", stock["customer"])
    delete_record(conn, "financer", save_financer(conn, name="HDFC"))
    assert (_count(conn, "part"), _count(conn, "part_stock_movement"), _count(conn, "vehicle")) == (0, 0, 1)


def test_used_records_cannot_be_deleted(conn, stock):
    sell_vehicle(conn, customer_id=stock["customer"], vehicle_id=stock["v1"])
    sell_parts(conn, lines=[PartLine(stock["part"], 1)])
    with pytest.raises(BusinessError, match="sold"):
        delete_record(conn, "vehicle", stock["v1"])
    with pytest.raises(BusinessError, match="sold or used"):
        delete_record(conn, "part", stock["part"])
    with pytest.raises(BusinessError, match="invoices"):
        delete_record(conn, "customer", stock["customer"])
    with pytest.raises(BusinessError, match="Deactivate"):
        delete_record(conn, "vehicle_model", stock["model"])
    with pytest.raises(ValueError):
        delete_record(conn, "invoice", 1)
    assert _count(conn, "part_stock_movement") == 2  # history untouched


def test_practice_mode_uses_separate_data(conn, stock):
    real_db = config.db_path()
    config.set_practice_mode(True)
    try:
        assert config.db_path() != real_db and config.db_path().parent == config.practice_dir()
        practice = open_database()
        assert _count(practice, "vehicle") == 0  # empty, separate database
        save_customer(practice, name="Dummy", phone="9000000000")
        practice.close()
        with pytest.raises(RuntimeError):
            config.delete_practice_data()  # not while in practice mode
    finally:
        config.set_practice_mode(False)
    assert config.db_path() == real_db
    assert _count(conn, "customer") == 1
    config.delete_practice_data()
    assert not config.practice_dir().exists()


# ---------------------------------------------------------------- screens

@pytest.fixture
def ui(monkeypatch):
    calls = {"messages": [], "restarts": 0, "typed": "ERASE"}
    monkeypatch.setattr(widgets, "show_error", lambda p, m: calls["messages"].append(("error", m)))
    monkeypatch.setattr(widgets, "show_info", lambda p, m: calls["messages"].append(("info", m)))
    monkeypatch.setattr(widgets, "confirm", lambda p, m: True)
    monkeypatch.setattr(widgets, "ask_text", lambda p, t, l: calls["typed"])
    monkeypatch.setattr(app_module, "restart_app", lambda: calls.__setitem__("restarts", calls["restarts"] + 1))
    return calls


def test_delete_buttons(qtbot, conn, stock, ui):
    window = MainWindow(conn)
    qtbot.addWidget(window)
    vehicles = window.page_by_name["Vehicles"]
    vehicles.refresh()
    vehicles.table.selectRow(0)
    vehicles.delete_selected()
    assert vehicles.table.rowCount() == 1

    sell_parts(conn, lines=[PartLine(stock["part"], 1)])
    parts = window.page_by_name["Parts"]
    parts.refresh()
    parts.table.selectRow(0)
    parts.delete_selected()
    assert "sold or used" in ui["messages"][-1][1] and parts.table.rowCount() == 1

    customers = CustomersTab(conn)
    qtbot.addWidget(customers)
    customers.table.selectRow(0)
    customers.delete()
    assert customers.table.rowCount() == 0


def test_switch_to_practice_mode_restarts(qtbot, conn, ui):
    page = SettingsPage(conn)
    qtbot.addWidget(page)
    page.switch_practice_mode(True)
    assert config.practice_mode() and ui["restarts"] == 1
    config.set_practice_mode(False)


def test_practice_banner(qtbot, conn):
    config.set_practice_mode(True)
    try:
        window = MainWindow(conn)
        qtbot.addWidget(window)
        assert "PRACTICE MODE" in window.windowTitle()
    finally:
        config.set_practice_mode(False)


def test_erase_needs_typed_confirmation_and_keeps_backup(qtbot, conn, stock, ui, db_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: db_path)
    page = SettingsPage(conn)
    qtbot.addWidget(page)
    ui["typed"] = "erase please"
    assert not page.erase_all()
    ui["typed"] = "erase"
    assert page.erase_all()
    assert not db_path.exists() and ui["restarts"] == 1
    assert any(p.name.endswith("before-erase.db") for p in config.backup_dir().iterdir())
