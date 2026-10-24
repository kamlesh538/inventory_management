"""Small reusable widgets and helpers shared by all screens."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHeaderView, QLineEdit,
    QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from jbhonda.core.errors import BusinessError
from jbhonda.core.money import format_inr, to_paise

GST_RATES_BP = (0, 500, 1200, 1800, 2800, 4000)


def format_date(iso: str | None) -> str:
    """'2026-10-24' -> '24-10-2026' (how dates are read in India)."""
    if not iso:
        return ""
    y, m, d = iso[:10].split("-")
    return f"{d}-{m}-{y}"


def show_error(parent: QWidget | None, message: str) -> None:
    QMessageBox.warning(parent, "Please check", message)


def show_info(parent: QWidget | None, message: str) -> None:
    QMessageBox.information(parent, "Done", message)


def confirm(parent: QWidget | None, message: str) -> bool:
    return QMessageBox.question(parent, "Please confirm", message) == QMessageBox.StandardButton.Yes


def open_file(path) -> None:
    """Open a file (e.g. an invoice PDF) in the PC's default program, ready to print."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


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
    sort_key: Callable[[Any], Any] | None = None  # e.g. paise for a ₹ column, ISO date for a dd-mm-yyyy one


_SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class _SortableItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine, theirs = self.data(_SORT_ROLE), other.data(_SORT_ROLE)
        if mine is None or theirs is None:
            return (mine is None) and (theirs is not None) or super().__lt__(other)
        return mine < theirs


class DataTable(QTableWidget):
    """Read-only, sortable table filled from a list of rows. Each row's id is kept for selection."""

    HIGHLIGHT = QColor("#fde8e8")

    def __init__(
        self, columns: Sequence[Column], parent: QWidget | None = None,
        highlight: Callable[[Any], bool] | None = None,
    ):
        super().__init__(0, len(columns), parent)
        self.columns = list(columns)
        self.highlight = highlight
        self.setHorizontalHeaderLabels([c.title for c in columns])
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        # Keep the order rows are given in until the user clicks a column header.
        self.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

    def fill(self, rows: Sequence[Any], row_id: Callable[[Any], int] = lambda r: r["id"]) -> None:
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            marked = self.highlight is not None and self.highlight(row)
            for c, column in enumerate(self.columns):
                value = column.get(row)
                item = _SortableItem("" if value is None else str(value))
                if column.sort_key is not None:
                    item.setData(_SORT_ROLE, column.sort_key(row))
                if column.align_right:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if marked:
                    item.setBackground(QBrush(self.HIGHLIGHT))
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
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(self.form)
        layout.addWidget(self.buttons)

    def save(self) -> None:
        raise NotImplementedError

    def try_save(self) -> bool:
        try:
            self.save()
        except (BusinessError, ValueError) as exc:
            show_error(self, str(exc))
            return False
        return True

    def accept(self) -> None:
        if self.try_save():
            super().accept()
