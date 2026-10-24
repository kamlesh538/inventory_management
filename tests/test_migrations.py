import sqlite3

import pytest

from jbhonda.db import migrate, open_database
from jbhonda.db.schema import MigrationError, available_migrations, current_version

EXPECTED_TABLES = {
    "shop_settings", "invoice_sequence", "vehicle_model", "customer", "financer", "insurer",
    "vehicle", "part", "part_stock_movement", "booking", "invoice", "invoice_item",
    "payment", "loan", "insurance_policy",
}


def test_fresh_database_gets_full_schema(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == EXPECTED_TABLES
    assert current_version(conn) == available_migrations()[-1][0]


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO vehicle (vehicle_model_id, chassis_number, engine_number, color)"
            " VALUES (999, 'X', 'Y', 'Red')"
        )


def test_schema_has_no_dangling_foreign_keys(conn):
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_shop_settings_seeded_with_one_row(conn):
    assert [r["shop_name"] for r in conn.execute("SELECT shop_name FROM shop_settings")] == ["Jai Bajrang Bali Honda"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO shop_settings (id, shop_name) VALUES (2, 'Other')")


def test_reopening_keeps_data_and_applies_nothing(seeded, db_path):
    seeded.close()
    conn = open_database(db_path)  # what happens after an app update or reinstall
    assert migrate(conn) == 0
    assert conn.execute("SELECT chassis_number FROM vehicle").fetchone()[0] == "ME4JF91AAPG000001"
    conn.close()


def _with_extra_migration(monkeypatch, sql):
    real = available_migrations()
    extra = (real[-1][0] + 1, f"{real[-1][0] + 1:04d}_extra.sql", sql)
    monkeypatch.setattr("jbhonda.db.schema.available_migrations", lambda: [*real, extra])
    return extra[0]


def test_update_backs_up_then_migrates_existing_data(seeded, db_path, monkeypatch):
    new_version = _with_extra_migration(monkeypatch, "ALTER TABLE vehicle ADD COLUMN fitness_cert TEXT;")
    backups = db_path.parent / "backups"

    assert migrate(seeded, backup_dir=backups) == 1

    assert current_version(seeded) == new_version
    assert seeded.execute("SELECT fitness_cert FROM vehicle").fetchone()[0] is None
    (backup,) = backups.iterdir()
    old = sqlite3.connect(backup)
    assert old.execute("SELECT chassis_number FROM vehicle").fetchone()[0] == "ME4JF91AAPG000001"
    assert old.execute("PRAGMA user_version").fetchone()[0] == new_version - 1
    old.close()


def test_failed_migration_changes_nothing(seeded, db_path, monkeypatch):
    version = current_version(seeded)
    _with_extra_migration(monkeypatch, "ALTER TABLE vehicle ADD COLUMN x TEXT; SELECT * FROM no_such_table;")

    with pytest.raises(MigrationError):
        migrate(seeded, backup_dir=db_path.parent / "backups")

    assert current_version(seeded) == version
    columns = {r["name"] for r in seeded.execute("PRAGMA table_info(vehicle)")}
    assert "x" not in columns
    assert not seeded.in_transaction


def test_newer_database_is_refused(conn):
    conn.execute("PRAGMA user_version = 999")
    with pytest.raises(MigrationError, match="newer version"):
        migrate(conn)
