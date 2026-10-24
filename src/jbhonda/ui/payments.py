"""A payment line (mode, amount, reference) used by the sales screens."""

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from jbhonda.core.sales import PaymentIn
from jbhonda.ui.widgets import ChoiceCombo, MoneyEdit

PAYMENT_MODE_CHOICES = [("Cash", "CASH"), ("UPI", "UPI"), ("Card", "CARD"), ("Cheque", "CHEQUE"),
                        ("Bank transfer", "BANK_TRANSFER")]


class PaymentRow(QWidget):
    def __init__(self, on_change, on_remove, parent: QWidget | None = None):
        super().__init__(parent)
        self.mode = ChoiceCombo(PAYMENT_MODE_CHOICES)
        self.amount = MoneyEdit()
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("UTR / cheque / card ref.")
        remove = QPushButton("X")
        remove.setObjectName("small")
        remove.setFixedWidth(32)
        remove.setToolTip("Remove this payment")
        self.typed = False  # True once the user types an amount; until then screens may fill it in
        self.amount.textEdited.connect(lambda: setattr(self, "typed", True))
        self.amount.textChanged.connect(on_change)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        remove.clicked.connect(lambda: on_remove(self))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.mode, 1)
        layout.addWidget(self.amount, 1)
        layout.addWidget(self.reference, 2)
        layout.addWidget(remove)
        self._mode_changed()

    def _mode_changed(self) -> None:
        self.reference.setEnabled(self.mode.value() != "CASH")

    def payment(self) -> PaymentIn | None:
        amount = self.amount.value()
        if not amount:
            return None
        return PaymentIn(self.mode.value(), amount, self.reference.text().strip() or None)
