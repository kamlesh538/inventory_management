import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import count
from pathlib import Path

from jbhonda import config
from jbhonda.db.schema import migrate

# SQL expression for "now" in the same format as the schema's created_at/updated_at defaults.
NOW = "strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')"

_savepoint_ids = count(1)


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


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block all-or-nothing. Nests safely: an inner block becomes a savepoint."""
    if conn.in_transaction:
        name = f"sp{next(_savepoint_ids)}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
