import sqlite3

from app import db


def test_missing_column_is_added_on_init(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    old_schema = db.SCHEMA.replace("    renamed_count             INTEGER NOT NULL DEFAULT 0,\n", "")
    conn.executescript(old_schema)
    conn.commit()
    conn.close()

    conn = db.open_database(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(scan_runs)")}
    assert "renamed_count" in cols
    db.init_schema(conn)  # idempotent
    conn.close()
