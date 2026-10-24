"""Screen tests, run headless. Message boxes are replaced so nothing blocks."""

import pytest

from jbhonda.core.settings import get_settings
from jbhonda.ui import widgets
from jbhonda.ui.main_window import MainWindow
from jbhonda.ui.masters_page import FinancerDialog, VehicleModelDialog


@pytest.fixture
def messages(monkeypatch):
    shown = []
    monkeypatch.setattr(widgets, "show_error", lambda parent, msg: shown.append(("error", msg)))
    monkeypatch.setattr(widgets, "show_info", lambda parent, msg: shown.append(("info", msg)))
    monkeypatch.setattr(widgets, "confirm", lambda parent, msg: True)
    return shown


@pytest.fixture
def window(qtbot, conn, messages):
    w = MainWindow(conn)
    qtbot.addWidget(w)
    return w


def test_navigation(window):
    for name in ("Home", "Masters", "Settings"):
        window.open_page(name)
        assert window.pages.currentWidget() is window.page_by_name[name]


def test_settings_page_saves(window, conn, messages):
    page = window.page_by_name["Settings"]
    page.inputs["gstin"].setText("27AAPFU0939F1ZV")
    page.inputs["city"].setText("Agra")
    page.terms.setPlainText("Goods once sold will not be taken back.")
    assert page.save()
    s = get_settings(conn)
    assert (s["city"], s["state_code"], s["invoice_terms"]) == ("Agra", "27", "Goods once sold will not be taken back.")
    assert page.inputs["state_code"].text() == "27"
    assert messages[-1][0] == "info"


def test_settings_page_shows_error_and_keeps_old_value(window, conn, messages):
    page = window.page_by_name["Settings"]
    page.inputs["gstin"].setText("BAD")
    assert not page.save()
    assert messages[-1][0] == "error" and "GSTIN" in messages[-1][1]
    assert get_settings(conn)["gstin"] is None


def test_add_and_edit_vehicle_model(qtbot, window, conn, messages):
    dialog = VehicleModelDialog(conn)
    qtbot.addWidget(dialog)
    dialog.model_name.setText("Activa 6G")
    dialog.variant.setText("DLX")
    dialog.engine_cc.setValue(110)
    dialog.price.setText("85000")
    dialog.accept()
    assert dialog.result() == dialog.DialogCode.Accepted
    row = conn.execute("SELECT * FROM vehicle_model").fetchone()
    assert (row["model_name"], row["gst_rate_bp"], row["ex_showroom_price"], row["category"]) == (
        "Activa 6G", 1800, 8500000, "SCOOTER")

    edit = VehicleModelDialog(conn, row["id"])
    qtbot.addWidget(edit)
    assert edit.price.text() == "85000.00"
    edit.price.setText("86500.50")
    edit.accept()
    assert conn.execute("SELECT ex_showroom_price FROM vehicle_model").fetchone()[0] == 8650050


def test_dialog_stays_open_on_error(qtbot, conn, messages):
    dialog = VehicleModelDialog(conn)
    qtbot.addWidget(dialog)
    dialog.model_name.setText("Activa 6G")
    dialog.accept()  # no price
    assert dialog.result() != dialog.DialogCode.Accepted
    assert messages == [("error", "Enter the ex-showroom price.")]
    assert conn.execute("SELECT count(*) FROM vehicle_model").fetchone()[0] == 0


def test_masters_tab_lists_and_deactivates(qtbot, window, conn, messages):
    dialog = FinancerDialog(conn)
    qtbot.addWidget(dialog)
    dialog.name.setText("HDFC Bank")
    dialog.accept()

    tab = window.page_by_name["Masters"].financers
    tab.refresh()
    assert tab.table.rowCount() == 1
    tab.table.selectRow(0)
    assert tab.toggle_button.text() == "Deactivate"
    tab.toggle_active()
    assert tab.table.rowCount() == 0  # inactive ones hidden by default
    tab.show_inactive.setChecked(True)
    assert tab.table.rowCount() == 1
    tab.table.selectRow(0)
    assert tab.toggle_button.text() == "Activate"


def test_home_page_shows_figures(window):
    home = window.page_by_name["Home"]
    home.refresh()
    assert home.title.text() == "Jai Bajrang Bali Honda"
    assert home.cards["sales_today"].value.text() == "₹0.00"
