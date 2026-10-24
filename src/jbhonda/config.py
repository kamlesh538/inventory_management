"""Where the app keeps its data.

The database lives outside the install folder so that uninstalling, reinstalling or
updating the app never touches it.

Practice mode uses a separate copy of everything (database, invoices, backups) in a
"practice" sub-folder, so dummy sales never mix with real ones and can be wiped freely.
"""

import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "JaiBajrangBaliHonda"
DB_FILE_NAME = "data.db"
_PRACTICE_FLAG = "practice-mode.flag"
_PRACTICE_DIR = "practice"


def root_dir() -> Path:
    """The real data folder, creating it if needed.

    Windows: C:\\ProgramData\\JaiBajrangBaliHonda (shared by all Windows users on the PC).
    Elsewhere (development): $XDG_DATA_HOME/JaiBajrangBaliHonda.
    JBH_DATA_DIR overrides both, for tests and development.
    """
    override = os.environ.get("JBH_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / APP_DIR_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def practice_mode() -> bool:
    return (root_dir() / _PRACTICE_FLAG).exists()


def set_practice_mode(on: bool) -> None:
    """Takes effect the next time the app starts."""
    flag = root_dir() / _PRACTICE_FLAG
    if on:
        flag.touch()
    else:
        flag.unlink(missing_ok=True)


def practice_dir() -> Path:
    return root_dir() / _PRACTICE_DIR


def delete_practice_data() -> None:
    """Remove everything made in practice mode. Only allowed while not in practice mode."""
    if practice_mode():
        raise RuntimeError("switch back to real data before deleting practice data")
    shutil.rmtree(practice_dir(), ignore_errors=True)


def data_dir() -> Path:
    """The folder in use: the real one, or the practice copy in practice mode."""
    path = practice_dir() if practice_mode() else root_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / DB_FILE_NAME


def backup_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path
