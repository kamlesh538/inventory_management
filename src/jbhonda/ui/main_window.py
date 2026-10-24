import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from jbhonda import __version__, config
from jbhonda.ui.bookings_page import BookingsPage
from jbhonda.ui.data_page import DataPage
from jbhonda.ui.home_page import HomePage
from jbhonda.ui.invoices_page import InvoicesPage
from jbhonda.ui.masters_page import MastersPage
from jbhonda.ui.parts_page import PartsPage
from jbhonda.ui.sell_parts_page import SellPartsPage
from jbhonda.ui.sell_vehicle_page import SellVehiclePage
from jbhonda.ui.settings_page import SettingsPage
from jbhonda.ui.vehicles_page import VehiclesPage


class MainWindow(QMainWindow):
    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self.conn = conn
        practice = config.practice_mode()
        self.setWindowTitle(f"Jai Bajrang Bali Honda - Inventory & Billing  (v{__version__})"
                            + ("  -  PRACTICE MODE" if practice else ""))
        self.resize(1200, 760)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(210)
        self.pages = QStackedWidget()
        self.page_by_name: dict[str, QWidget] = {}
        self.sell_vehicle = SellVehiclePage(conn)
        for name, page in (
            ("Home", HomePage(conn)),
            ("Sell Vehicle", self.sell_vehicle),
            ("Sell Parts", SellPartsPage(conn)),
            ("Bookings", BookingsPage(conn, on_sell=self.sell_booking)),
            ("Invoices", InvoicesPage(conn)),
            ("Vehicles", VehiclesPage(conn)),
            ("Parts", PartsPage(conn)),
            ("Masters", MastersPage(conn)),
            ("Export & Backup", DataPage(conn)),
            ("Settings", SettingsPage(conn)),
        ):
            self.nav.addItem(name)
            self.pages.addWidget(page)
            self.page_by_name[name] = page
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.nav)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        if practice:
            banner = QLabel("PRACTICE MODE  -  nothing here is real. Switch back to real data in Settings.")
            banner.setObjectName("practiceBanner")
            banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
            right.addWidget(banner)
        right.addWidget(self.pages, 1)
        layout.addLayout(right, 1)
        self.setCentralWidget(central)

    def sell_booking(self, booking_id: int) -> None:
        self.open_page("Sell Vehicle")
        self.sell_vehicle.load_booking(booking_id)

    def open_page(self, name: str) -> None:
        self.nav.setCurrentRow(list(self.page_by_name).index(name))
