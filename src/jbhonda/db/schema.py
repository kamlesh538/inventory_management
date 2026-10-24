"""Schema migrations.

Each file in ``migrations/`` is named ``NNNN_description.sql`` and is applied once, in
order. ``PRAGMA user_version`` records the last applied number. Migrations must only ever
be added, never edited after release: an installed copy may already have run them.

Before migrating a database that already holds data, a backup copy is taken, so a failed
update can always be rolled back by hand.
"""

import re
import sqlite3
from datetime import datetime
from importlib import resources
from pathlib import Path

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    pass


def available_migrations() -> list[tuple[int, str, str]]:
    """Return (version, file name, sql) for every bundled migration, in order."""
    found = []
    for entry in resources.files("jbhonda.db.migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [v for v, _, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"Migrations must be numbered 0001, 0002, ... without gaps: {versions}")
    return found


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, backup_dir: Path | None = None) -> int:
    """Apply pending migrations. Returns the number applied."""
    migrations = available_migrations()
    version = current_version(conn)
    latest = migrations[-1][0] if migrations else 0
    if version > latest:
        raise MigrationError(
            f"The database is from a newer version of the app (schema {version}, "
            f"this app knows up to {latest}). Install the latest version."
        )
    pending = [m for m in migrations if m[0] > version]
    if not pending:
        return 0

    if version > 0 and backup_dir is not None:
        backup_database(conn, backup_dir, label=f"before-schema-{pending[0][0]:04d}")

    # Rebuilding a table (the only way to change its CHECK rules in SQLite) needs foreign keys
    # off; it can't be switched inside a transaction, so do it around all migrations and
    # verify every reference before each commit instead.
    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for number, name, sql in pending:
            # executescript() commits any open transaction first, so open our own: either the
            # whole migration applies, or none of it does.
            try:
                conn.executescript(f"BEGIN;\n{sql}")
                broken = conn.execute("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise MigrationError(f"{len(broken)} broken references, e.g. {tuple(broken[0])}")
                conn.execute(f"PRAGMA user_version = {number}")
                conn.execute("COMMIT")
            except (sqlite3.Error, MigrationError) as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise MigrationError(f"Migration {name} failed: {exc}") from exc
    finally:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if fk_was_on else 'OFF'}")
    return len(pending)


def backup_database(conn: sqlite3.Connection, backup_dir: Path, label: str) -> Path:
    """Write a consistent copy of the database using SQLite's online backup API."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"data-{stamp}-{label}.db"
    with sqlite3.connect(target) as dest:
        conn.backup(dest)
    dest.close()
    return target
