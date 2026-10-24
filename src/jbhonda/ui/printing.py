"""Printing PDFs (invoices) straight from the app, without needing a PDF reader installed."""

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QDialog, QWidget

PRINT_DPI = 300  # sharp on paper, without building huge page images


def print_pdf(parent: QWidget | None, path: Path) -> bool:
    """Show the Windows print dialog (printer, copies) and print the PDF on A4. Returns True if printed."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setDocName(path.stem)
    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle(f"Print {path.stem}")
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    render_to_printer(path, printer)
    return True


def render_to_printer(path: Path, printer: QPrinter) -> int:
    """Draw every page of the PDF onto the printer, fitted to the printable area. Returns pages printed."""
    doc = QPdfDocument()
    if doc.load(str(path)) != QPdfDocument.Error.None_:
        raise OSError(f"Could not read {path}")
    painter = QPainter()
    if not painter.begin(printer):
        raise OSError("Could not start printing. Check the printer is switched on and connected.")
    try:
        for page in range(doc.pageCount()):
            if page:
                printer.newPage()
            points = doc.pagePointSize(page)
            image = doc.render(page, QSize(round(points.width() * PRINT_DPI / 72),
                                           round(points.height() * PRINT_DPI / 72)))
            area = painter.viewport()
            fitted = image.size().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x = area.x() + (area.width() - fitted.width()) // 2
            painter.drawImage(QRect(x, area.y(), fitted.width(), fitted.height()), image)
    finally:
        painter.end()
    return doc.pageCount()
