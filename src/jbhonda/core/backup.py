"""Backups of the whole database, and restoring from one.

- An automatic backup is taken once a day when the app starts; the newest 30 are kept.
- "Backup now" can write to any folder, e.g. a pen drive.
- Restoring first backs up the current data, so a restore can itself be undone.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from jbhonda import config
from jbhonda.core.errors import BusinessError
from jbhonda.db.schema import available_migrations, backup_database, current_version, migrate

KEEP_AUTO = 30
_REQUIRED_TABLES = {"shop_settings", "vehicle", "part", "invoice", "invoice_item", "payment"}


@dataclass(frozen=True)
class BackupFile:
    path: Path
    taken_at: datetime
    size_bytes: int
    automatic: bool


def list_backups(folder: Path | None = None) -> list[BackupFile]:
    """Backups in a folder (default: the app's backup folder), newest first."""
    folder = folder or config.backup_dir()
    found = []
    for path in folder.glob("data-*.db"):
        parts = path.stem.split("-")  # data-YYYYMMDD-HHMMSS-label
        try:
            taken = datetime.strptime(f"{parts[1]}{parts[2]}", "%Y%m%d%H%M%S")
        except (IndexError, ValueError):
            continue
        found.append(BackupFile(path, taken, path.stat().st_size, path.stem.endswith("-auto")))
    return sorted(found, key=lambda b: b.taken_at, reverse=True)


def backup_now(conn: sqlite3.Connection, folder: Path | None = None) -> Path:
    folder = folder or config.backup_dir()
    try:
        return backup_database(conn, folder, label="manual")
    except (OSError, sqlite3.Error) as exc:
        raise BusinessError(f"Could not write the backup to {folder}: {exc}") from exc


def auto_backup(conn: sqlite3.Connection, today: date | None = None) -> Path | None:
    """Take today's automatic backup if there isn't one yet, then prune old ones. Returns the new file."""
    today = today or date.today()
    autos = [b for b in list_backups() if b.automatic]
    made = None
    if not any(b.taken_at.date() == today for b in autos):
        made = backup_database(conn, config.backup_dir(), label="auto")
        autos = [b for b in list_backups() if b.automatic]
    for old in autos[KEEP_AUTO:]:
        old.path.unlink(missing_ok=True)
    return made


def check_backup_file(path: Path) -> int:
    """Make sure a file is a backup of this app that this version can open. Returns its schema version."""
    try:
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            version = current_version(src)
            ok = src.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            src.close()
    except sqlite3.Error:
        raise BusinessError("That file is not a backup of this app (or it is damaged).") from None
    if not _REQUIRED_TABLES <= tables:
        raise BusinessError("That file is not a backup of this app.")
    if not ok:
        raise BusinessError("That backup file is damaged and cannot be restored.")
    if version > available_migrations()[-1][0]:
        raise BusinessError("That backup is from a newer version of the app. Install the latest version first.")
    return version


def restore(conn: sqlite3.Connection, path: Path) -> Path:
    """Replace all current data with the backup's. Returns the safety backup taken just before.

    An older backup is upgraded to the current schema straight after restoring.
    """
    check_backup_file(path)
    safety = backup_database(conn, config.backup_dir(), label="before-restore")
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        src.backup(conn)
    finally:
        src.close()
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn, backup_dir=None)
    return safety
