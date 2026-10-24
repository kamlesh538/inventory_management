"""Entry point. The window comes in a later step; for now this prepares the database."""

from jbhonda import config
from jbhonda.db import open_database
from jbhonda.db.schema import current_version


def main() -> None:
    conn = open_database()
    print(f"Database ready at {config.db_path()} (schema version {current_version(conn)})")
    conn.close()


if __name__ == "__main__":
    main()
