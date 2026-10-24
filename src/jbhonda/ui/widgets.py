"""Small reusable widgets and helpers shared by all screens."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHeaderView, QLineEdit,
    QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from jbhonda.core.errors import BusinessError
from jbhonda.core.money import format_inr, to_paise

GST_RATES_BP = (0, 500, 1200, 1800, 2800, 4000)


def show_error(parent: QWidget | None, message: str) -> None:
    QMessageBox.warning(parent, "Please check", message)


def show_info(parent: QWidget | None, message: str) -> None:
    QMessageBox.information(parent, "Done", message)


def confirm(parent: QWidget | None, message: str) -> bool:
    return QMessageBox.question(parent, "Please confirm", message) == QMessageBox.StandardButton.Yes


class MoneyEdit(QLineEdit):
    """Rupee amount input. value() returns paise, or None when empty."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d{0,9}(\.\d{0,2})?$"), self))
        self.setPlaceholderText("0.00")
        self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def value(self) -> int | None:
        text = self.text().strip()
        return to_paise(text) if text else None

    def setValue(self, paise: int | None) -> None:
        self.setText("" if paise is None else format_inr(paise, symbol=False).replace(",", ""))


class GstRateCombo(QComboBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        for bp in GST_RATES_BP:
            self.addItem(f"{bp / 100:g}%", bp)

    def value(self) -> int:
        return self.currentData()

    def setValue(self, bp: int) -> None:
        index = self.findData(bp)
        if index < 0:  # a rate outside the usual list, e.g. set before a GST change
            self.addItem(f"{bp / 100:g}%", bp)
            index = self.count() - 1
        self.setCurrentIndex(index)


class ChoiceCombo(QComboBox):
    """Combo whose items carry a stored value, e.g. ('Scooter', 'SCOOTER')."""

    def __init__(self, choices: Sequence[tuple[str, Any]], parent: QWidget | None = None):
        super().__init__(parent)
        for label, value in choices:
            self.addItem(label, value)

    def value(self) -> Any:
        return self.currentData()

    def setValue(self, value: Any) -> None:
        index = self.findData(value)
        if index >= 0:
            self.setCurrentIndex(index)


@dataclass(frozen=True)
class Column:
    title: str
    get: Callable[[Any], Any]
    align_right: bool = False


class DataTable(QTableWidget):
    """Read-only, sortable table filled from a list of rows. Each row's id is kept for selection."""

    def __init__(self, columns: Sequence[Column], parent: QWidget | None = None):
        super().__init__(0, len(columns), parent)
        self.columns = list(columns)
        self.setHorizontalHeaderLabels([c.title for c in columns])
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)

    def fill(self, rows: Sequence[Any], row_id: Callable[[Any], int] = lambda r: r["id"]) -> None:
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, column in enumerate(self.columns):
                value = column.get(row)
                item = QTableWidgetItem("" if value is None else str(value))
                if column.align_right:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row_id(row))
                self.setItem(r, c, item)
        self.setSortingEnabled(True)

    def selected_id(self) -> int | None:
        rows = self.selectionModel().selectedRows()
        if not rows:
            return None
        return self.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)


class FormDialog(QDialog):
    """A dialog with a form and Save/Cancel. Subclasses implement save().

    If save() raises BusinessError (or ValueError from bad input), the message is shown and
    the dialog stays open so the user can fix it.
    """

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.form = QFormLayout()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(self.form)
        layout.addWidget(buttons)

    def save(self) -> None:
        raise NotImplementedError

    def accept(self) -> None:
        try:
            self.save()
        except (BusinessError, ValueError) as exc:
            show_error(self, str(exc))
            return
        super().accept()
