# Build Plan

Target: Windows 10/11, 64-bit. Single installer `.exe`; data in
`C:\ProgramData\JaiBajrangBaliHonda\` so reinstalls and updates never erase it.

## Stack

| Part | Choice |
|---|---|
| Language | Python 3.12, managed with uv |
| Screens | PySide6 (Qt 6, LGPL) |
| Database | SQLite via built-in `sqlite3`; numbered SQL migrations in `src/jbhonda/db/migrations/` |
| Invoice PDFs | ReportLab |
| Export | openpyxl (Excel) + CSV |
| Packaging | PyInstaller → Inno Setup installer (built on Windows) |

## Steps

1. [x] Project skeleton, database, migrations, full schema, tests
2. [x] Core logic: GST maths, invoice numbering per financial year, stock ledger, sell/cancel rules
3. [x] Main window, shop settings, masters (vehicle models, financers, insurers)
4. [x] Vehicles: add / show
5. [x] Parts: add / stock-in / show, low-stock highlight
6. [x] Sell vehicle: customer, booking, payments, loan, insurance → invoice
7. [x] Sell parts: counter sale
8. [x] Invoices screen (list, receive balance, loan received, cancel) + invoice PDFs
9. [x] Export (Excel/CSV), backup & restore
10. [x] Installer (PyInstaller + Inno Setup, GitHub Actions build) - still to test on a real Windows 10 PC

## Development

```
uv sync
uv run pytest
JBH_DATA_DIR=./devdata uv run jbhonda
```
