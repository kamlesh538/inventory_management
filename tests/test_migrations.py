import sqlite3

import pytest

from jbhonda.db import migrate, open_database
from jbhonda.db.schema import MigrationError, available_migrations, current_version

EXPECTED_TABLES = {
    "shop_settings", "invoice_sequence", "vehicle_model", "customer", "financer", "insurer",
    "vehicle", "part", "part_stock_movement", "booking", "invoice", "invoice_item",
    "payment", "loan", "insurance_policy",
    "labour_item", "service_job", "service_job_part", "service_job_labour",
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


def test_upgrade_from_v1_keeps_all_data(tmp_path, monkeypatch):
    """A shop running schema 1 with real sales upgrades to the latest schema without losing anything."""
    from jbhonda.core.customers import save_customer
    from jbhonda.core.inventory import save_part, save_vehicle, save_vehicle_model
    from jbhonda.core.sales import PartLine, PaymentIn, cancel_invoice, complete_part_sale
    from jbhonda.core.vehicle_sale import complete_vehicle_sale
    from jbhonda.db import connect, schema

    real = available_migrations()
    monkeypatch.setattr(schema, "available_migrations", lambda: real[:1])
    conn = connect(tmp_path / "old.db")
    migrate(conn)
    m = save_vehicle_model(conn, model_name="Activa", category="SCOOTER", gst_rate_bp=1800, ex_showroom_price=8500000)
    v = save_vehicle(conn, vehicle_model_id=m, chassis_number="C1", engine_number="E1", color="Red")
    c = save_customer(conn, name="Ram", phone="9876543210")
    p = save_part(conn, part_number="P1", name="Plug", hsn_code="8511", gst_rate_bp=1800, sale_price=14000,
                  opening_qty=10)
    complete_vehicle_sale(conn, customer_id=c, vehicle_id=v, accessories=[PartLine(p, 1)],
                          payments=[PaymentIn("CASH", 8514000)])
    cancel_invoice(conn, complete_part_sale(conn, lines=[PartLine(p, 2)], payments=[PaymentIn("CASH", 28000)]), "x")
    tables = ("invoice", "invoice_item", "part_stock_movement", "invoice_sequence", "payment", "vehicle", "part")
    before = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in tables}

    monkeypatch.setattr(schema, "available_migrations", lambda: real)
    assert migrate(conn, backup_dir=tmp_path / "backups") == len(real) - 1
    for t in tables:
        after = [tuple(r)[:len(before[t][0])] for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")]
        assert after == before[t], t
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(list((tmp_path / "backups").iterdir())) == 1  # backed up before upgrading
    # The rules still hold after the rebuild
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM invoice")
    conn.close()
