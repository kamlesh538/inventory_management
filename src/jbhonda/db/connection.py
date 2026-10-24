import sqlite3
from pathlib import Path

from jbhonda import config
from jbhonda.db.schema import migrate


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a connection with the settings every part of the app relies on."""
    conn = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT only
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")  # money data: favour durability over speed
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def open_database(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the app database, applying any pending migrations first."""
    path = Path(path) if path is not None else config.db_path()
    conn = connect(path)
    migrate(conn, backup_dir=path.parent / "backups")
    return conn
