"""Export data to Excel/CSV, and back up / restore the database."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDateEdit, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QVBoxLayout, QWidget,
)

from jbhonda import config
from jbhonda.core.backup import backup_now, list_backups, restore
from jbhonda.core.errors import BusinessError
from jbhonda.core.numbering import financial_year
from jbhonda.reports.export import REPORTS, build_reports, write_csv, write_xlsx
from jbhonda.ui import widgets
from jbhonda.ui.widgets import ChoiceCombo, Column, DataTable


def _choose_folder(parent: QWidget, title: str) -> Path | None:
    folder = QFileDialog.getExistingDirectory(parent, title)
    return Path(folder) if folder else None


def _choose_backup_file(parent: QWidget) -> Path | None:
    path, _ = QFileDialog.getOpenFileName(parent, "Choose a backup to restore", str(config.backup_dir()),
                                          "Backups (*.db)")
    return Path(path) if path else None


def _choose_save_path(parent: QWidget, suggested: str) -> Path | None:
    path, _ = QFileDialog.getSaveFileName(parent, "Save Excel file", str(Path.home() / suggested),
                                          "Excel files (*.xlsx)")
    return Path(path) if path else None


class DataPage(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        title = QLabel("Export & backup")
        title.setObjectName("pageTitle")

        # -- export
        export_box = QGroupBox("Export data")
        grid = QGridLayout()
        self.report_boxes: dict[str, QCheckBox] = {}
        for i, (key, (label, _, _)) in enumerate(REPORTS.items()):
            box = QCheckBox(label)
            box.setChecked(key in ("sales", "gst"))
            self.report_boxes[key] = box
            grid.addWidget(box, i // 2, i % 2)
        today = QDate.currentDate()
        self.period = ChoiceCombo([("This month", "MONTH"), ("Last month", "LAST_MONTH"),
                                   ("This financial year", "FY"), ("Custom dates", "CUSTOM"), ("All dates", "ALL")])
        self.date_from = QDateEdit(QDate(today.year(), today.month(), 1))
        self.date_to = QDateEdit(today)
        for d in (self.date_from, self.date_to):
            d.setDisplayFormat("dd-MM-yyyy")
            d.setCalendarPopup(True)
        self.period.currentIndexChanged.connect(self._period_changed)
        self.as_excel = QRadioButton("Excel (one file, a sheet per report)")
        self.as_csv = QRadioButton("CSV (one file per report)")
        self.as_excel.setChecked(True)
        self.export_button = QPushButton("Export")
        self.export_button.setObjectName("primary")
        self.export_button.clicked.connect(self.export)

        dates = QHBoxLayout()
        dates.addWidget(QLabel("Period"))
        dates.addWidget(self.period)
        dates.addWidget(QLabel("from"))
        dates.addWidget(self.date_from)
        dates.addWidget(QLabel("to"))
        dates.addWidget(self.date_to)
        dates.addStretch()
        formats = QHBoxLayout()
        formats.addWidget(self.as_excel)
        formats.addWidget(self.as_csv)
        formats.addStretch()
        formats.addWidget(self.export_button)
        note = QLabel("The period applies to sales, GST, payments and bookings. Stock and customer lists are as of now.")
        note.setObjectName("cardCaption")
        v = QVBoxLayout(export_box)
        v.addLayout(grid)
        v.addLayout(dates)
        v.addWidget(note)
        v.addLayout(formats)

        # -- backup
        backup_box = QGroupBox("Backup && restore")
        self.backup_button = QPushButton("Backup now")
        self.backup_button.setObjectName("primary")
        self.backup_to_button = QPushButton("Backup to pen drive / folder...")
        self.restore_button = QPushButton("Restore from backup...")
        self.open_folder_button = QPushButton("Open backup folder")
        self.backup_button.clicked.connect(lambda: self.make_backup(None))
        self.backup_to_button.clicked.connect(self.backup_to_folder)
        self.restore_button.clicked.connect(self.restore_from_file)
        self.open_folder_button.clicked.connect(lambda: widgets.open_file(config.backup_dir()))
        self.backups = DataTable([
            Column("Taken at", lambda b: b.taken_at.strftime("%d-%m-%Y %H:%M"), sort_key=lambda b: b.taken_at),
            Column("Kind", lambda b: "Automatic (daily)" if b.automatic else b.path.stem.split("-", 3)[-1]
                   .replace("-", " ").capitalize()),
            Column("Size", lambda b: f"{b.size_bytes / 1024:,.0f} KB", align_right=True, sort_key=lambda b: b.size_bytes),
            Column("File", lambda b: b.path.name),
        ])
        info = QLabel(
            f"Your data: {config.db_path()}\n"
            "A backup is taken automatically every day when the app starts (the last 30 are kept), and before "
            "every update or restore. Keep a copy on a pen drive too, in case this computer fails."
        )
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        buttons = QHBoxLayout()
        for b in (self.backup_button, self.backup_to_button, self.restore_button, self.open_folder_button):
            buttons.addWidget(b)
        buttons.addStretch()
        v = QVBoxLayout(backup_box)
        v.addWidget(info)
        v.addLayout(buttons)
        v.addWidget(self.backups)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(export_box)
        layout.addWidget(backup_box, 1)
        self._period_changed()
        self.refresh()

    # ------------------------------------------------------------ export

    def _period_changed(self) -> None:
        today = date.today()
        choice = self.period.value()
        custom = choice == "CUSTOM"
        self.date_from.setEnabled(custom)
        self.date_to.setEnabled(custom)
        if choice == "MONTH":
            start, end = today.replace(day=1), today
        elif choice == "LAST_MONTH":
            end = today.replace(day=1) - timedelta(days=1)
            start = end.replace(day=1)
        elif choice == "FY":
            start, end = date(int(financial_year(today)[:4]), 4, 1), today
        else:
            return
        self.date_from.setDate(start)
        self.date_to.setDate(end)

    def period_range(self) -> tuple[date | None, date | None]:
        if self.period.value() == "ALL":
            return None, None
        return self.date_from.date().toPython(), self.date_to.date().toPython()

    def export(self) -> list[Path]:
        keys = [k for k, box in self.report_boxes.items() if box.isChecked()]
        try:
            if not keys:
                raise BusinessError("Tick at least one report to export.")
            date_from, date_to = self.period_range()
            if date_from and date_to and date_from > date_to:
                raise BusinessError("The 'from' date is after the 'to' date.")
            span = (f"{date_from:%d-%m-%Y} to {date_to:%d-%m-%Y}" if date_from else "all dates")
            stem = f"JBHonda {date_from:%Y-%m-%d} to {date_to:%Y-%m-%d}" if date_from else "JBHonda all dates"
            reports = build_reports(self.conn, keys, date_from, date_to)
            if self.as_excel.isChecked():
                target = _choose_save_path(self, stem + ".xlsx")
                if target is None:
                    return []
                written = [write_xlsx(target, reports, subtitle=span)]
            else:
                folder = _choose_folder(self, "Choose a folder for the CSV files")
                if folder is None:
                    return []
                written = write_csv(folder, reports, stem)
        except (BusinessError, OSError) as exc:
            widgets.show_error(self, str(exc))
            return []
        rows = sum(len(r.rows) for r in reports)
        if widgets.confirm(self, f"Exported {rows} rows to:\n{written[0] if len(written) == 1 else written[0].parent}"
                                 "\n\nOpen it now?"):
            widgets.open_file(written[0] if len(written) == 1 else written[0].parent)
        return written

    # ------------------------------------------------------------ backup

    def refresh(self) -> None:
        self.backups.fill(list_backups(), row_id=lambda b: 0)

    def make_backup(self, folder: Path | None) -> Path | None:
        try:
            path = backup_now(self.conn, folder)
        except BusinessError as exc:
            widgets.show_error(self, str(exc))
            return None
        self.refresh()
        widgets.show_info(self, f"Backup saved:\n{path}")
        return path

    def backup_to_folder(self) -> None:
        folder = _choose_folder(self, "Choose the pen drive or folder for the backup")
        if folder is not None:
            self.make_backup(folder)

    def restore_from_file(self, path: Path | None = None) -> bool:
        path = path or _choose_backup_file(self)
        if path is None:
            return False
        if not widgets.confirm(
            self, f"Replace ALL current data with the backup\n{path.name}?\n\n"
                  "A backup of the current data is taken first, so this can be undone.",
        ):
            return False
        try:
            safety = restore(self.conn, path)
        except BusinessError as exc:
            widgets.show_error(self, str(exc))
            return False
        widgets.show_info(
            self, f"Data restored from {path.name}.\n\nThe previous data was saved as {safety.name}.\n\n"
                  "The app will now close. Open it again to continue.")
        app = QApplication.instance()
        if app is not None and self.window().isVisible():
            app.quit()
        return True

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)
