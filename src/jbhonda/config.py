"""Where the app keeps its data.

The database lives outside the install folder so that uninstalling, reinstalling or
updating the app never touches it.
"""

import os
import sys
from pathlib import Path

APP_DIR_NAME = "JaiBajrangBaliHonda"
DB_FILE_NAME = "data.db"


def data_dir() -> Path:
    """Return the data directory, creating it if needed.

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


def db_path() -> Path:
    return data_dir() / DB_FILE_NAME


def backup_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path
