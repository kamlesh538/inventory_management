import sqlite3

from PySide6.QtWidgets import QHBoxLayout, QListWidget, QMainWindow, QStackedWidget, QWidget

from jbhonda import __version__
from jbhonda.ui.home_page import HomePage
from jbhonda.ui.masters_page import MastersPage
from jbhonda.ui.settings_page import SettingsPage
from jbhonda.ui.vehicles_page import VehiclesPage


class MainWindow(QMainWindow):
    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self.conn = conn
        self.setWindowTitle(f"Jai Bajrang Bali Honda - Inventory & Billing  (v{__version__})")
        self.resize(1200, 760)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(210)
        self.pages = QStackedWidget()
        self.page_by_name: dict[str, QWidget] = {}
        for name, page in (
            ("Home", HomePage(conn)),
            ("Vehicles", VehiclesPage(conn)),
            ("Masters", MastersPage(conn)),
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
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)

    def open_page(self, name: str) -> None:
        self.nav.setCurrentRow(list(self.page_by_name).index(name))
