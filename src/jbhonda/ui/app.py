"""Starts the desktop app: logging, database, styling, main window, crash reporting."""

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from jbhonda import config
from jbhonda.core.errors import BusinessError
from jbhonda.db import open_database
from jbhonda.db.schema import MigrationError

log = logging.getLogger("jbhonda")

STYLE = """
QWidget { font-size: 10.5pt; }
QLabel#pageTitle { font-size: 16pt; font-weight: 600; padding: 4px 0 10px 0; }
QListWidget#nav { background: #1f2933; color: #e4e7eb; border: none; outline: 0; font-size: 11.5pt; padding-top: 12px; }
QListWidget#nav::item { padding: 11px 18px; }
QListWidget#nav::item:selected { background: #cc0000; color: white; }
QListWidget#nav::item:hover:!selected { background: #323f4b; }
QPushButton { padding: 6px 14px; }
QLineEdit:read-only { background: #eef0f3; color: #52606d; }
QPushButton#primary { background: #cc0000; color: white; border: 1px solid #a30000; border-radius: 3px; }
QPushButton#primary:hover { background: #b00000; }
QPushButton#primary:disabled { background: #e8a0a0; }
QFrame#card { background: white; border: 1px solid #d9dde3; border-radius: 6px; padding: 10px; }
QFrame#card[alert="true"] { border: 2px solid #cc0000; }
QLabel#cardValue { font-size: 20pt; font-weight: 600; }
QLabel#cardCaption { color: #52606d; }
QLabel#summaryValue { qproperty-alignment: AlignRight; }
QLabel#summaryTotal { qproperty-alignment: AlignRight; font-weight: 600; font-size: 12pt; }
QLabel#summaryValue[alert="true"] { color: #cc0000; font-weight: 600; }
QLabel#hint { background: #fff4e5; border: 1px solid #f0c36d; border-radius: 3px; padding: 6px; }
"""


def _setup_logging() -> None:
    log_dir = config.data_dir() / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _excepthook(exc_type, exc, tb) -> None:
    """Show unexpected errors instead of silently closing, and log them for support."""
    if issubclass(exc_type, BusinessError):
        QMessageBox.warning(None, "Please check", str(exc))
        return
    log.error("Unexpected error", exc_info=(exc_type, exc, tb))
    QMessageBox.critical(
        None, "Something went wrong",
        "An unexpected error occurred. Your saved data is safe.\n\n"
        f"{exc_type.__name__}: {exc}\n\n"
        f"Details are saved in:\n{config.data_dir() / 'logs' / 'app.log'}",
    )


def run() -> int:
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Jai Bajrang Bali Honda")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI"))
    app.setStyleSheet(STYLE)
    sys.excepthook = _excepthook

    try:
        conn = open_database()
    except MigrationError as exc:
        log.error("Database upgrade failed: %s", exc)
        QMessageBox.critical(None, "Cannot open data", str(exc))
        return 1
    except Exception:
        log.error("Cannot open database\n%s", traceback.format_exc())
        QMessageBox.critical(None, "Cannot open data", f"Could not open the database at\n{config.db_path()}")
        return 1
    log.info("Started; database %s", config.db_path())

    from jbhonda.ui.main_window import MainWindow  # after QApplication exists

    window = MainWindow(conn)
    window.showMaximized()
    code = app.exec()
    conn.close()
    return code
