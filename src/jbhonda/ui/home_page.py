import sqlite3

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from jbhonda.core.dashboard import summary
from jbhonda.core.money import format_inr
from jbhonda.core.settings import get_settings


class StatCard(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.value = QLabel("-")
        self.value.setObjectName("cardValue")
        caption = QLabel(title)
        caption.setObjectName("cardCaption")
        layout = QVBoxLayout(self)
        layout.addWidget(self.value)
        layout.addWidget(caption)


class HomePage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        self.cards = {
            "sales_today": StatCard("Sales today"),
            "sales_this_month": StatCard("Sales this month"),
            "vehicles_sold_this_month": StatCard("Vehicles sold this month"),
            "vehicles_in_stock": StatCard("Vehicles in stock"),
            "vehicles_booked": StatCard("Vehicles booked"),
            "low_stock_parts": StatCard("Parts low on stock"),
        }
        grid = QGridLayout()
        for i, card in enumerate(self.cards.values()):
            grid.addWidget(card, i // 3, i % 3)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addLayout(grid)
        layout.addStretch()
        self.refresh()

    def refresh(self) -> None:
        self.title.setText(get_settings(self.conn)["shop_name"])
        s = summary(self.conn)
        for key, card in self.cards.items():
            value = getattr(s, key)
            card.value.setText(format_inr(value) if key.startswith("sales") else str(value))
        self.cards["low_stock_parts"].setProperty("alert", s.low_stock_parts > 0)
        self.cards["low_stock_parts"].style().polish(self.cards["low_stock_parts"])

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)
