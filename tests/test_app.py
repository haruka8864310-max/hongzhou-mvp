import sqlite3
from pathlib import Path

import app


def _seed_database(db_path: Path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO customers(name, phone, address) VALUES (?, ?, ?)",
            ("測試客戶", "0912-345-678", "台中市"),
        )
        conn.execute(
            "INSERT INTO trades(date, customer_id, kind, material, kg, unit_price, transport, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-11-01", 1, "buy", "廢鋼", 1200.0, 12.5, 800, "測試新增"),
        )
        conn.commit()


def test_database_setup_creates_tables(tmp_path):
    root = tmp_path / "hongzhou-data"
    db_path = root / "hongzhou.db"
    app.ROOT = root
    app.DB = db_path
    app.BACKUPS = root / "backups"
    app.BACKUPS.mkdir(parents=True, exist_ok=True)

    app.setup_database()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"prices", "customers", "trades"}.issubset(tables)


def test_backup_and_restore_round_trip(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True)
    app.ROOT = source_root
    app.DB = source_root / "hongzhou.db"
    app.BACKUPS = source_root / "backups"
    app.BACKUPS.mkdir(parents=True, exist_ok=True)
    app.setup_database()
    _seed_database(app.DB)

    backup_path = app.backup_database()
    assert backup_path.exists()

    target_root = tmp_path / "restored"
    target_root.mkdir(parents=True)
    app.ROOT = target_root
    app.DB = target_root / "hongzhou.db"
    app.BACKUPS = target_root / "backups"
    app.BACKUPS.mkdir(parents=True, exist_ok=True)

    app.restore_database(backup_path)

    with sqlite3.connect(app.DB) as conn:
        customer_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert customer_count == 1
        assert trade_count == 1
