import sqlite3
from pathlib import Path

import pytest

from app import db

IMAGE_ROW = {
    "content_hash": "a" * 64,
    "file_path": "x.png",
    "dir_path": "",
    "file_name": "x.png",
    "file_size": 1,
    "file_mtime": "2026-01-01T00:00:00Z",
    "presence": "active",
    "thumbnail_status": "pending",
    "extraction_status": "none",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}


def insert_image(conn: sqlite3.Connection, **overrides) -> int:
    row = {**IMAGE_ROW, **overrides}
    cols = ", ".join(row)
    params = ", ".join(f":{c}" for c in row)
    cur = conn.execute(f"INSERT INTO images ({cols}) VALUES ({params})", row)
    return cur.lastrowid


@pytest.fixture
def conn(tmp_path: Path):
    c = db.open_database(tmp_path / "nested" / "images.db")
    yield c
    c.close()


def test_pragmas(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_schema_objects_exist(conn):
    names = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
    }
    assert {"images", "image_raw_metadata", "scan_runs"} <= names
    assert {
        "uk_images_content_hash",
        "idx_images_cursor",
        "idx_images_favorite",
        "idx_images_dir",
        "idx_images_presence",
    } <= names


def test_init_schema_is_idempotent(conn):
    db.init_schema(conn)
    db.init_schema(conn)


def test_raw_metadata_cascades_on_delete(conn):
    image_id = insert_image(conn)
    conn.execute(
        "INSERT INTO image_raw_metadata (image_id, prompt_json) VALUES (?, ?)",
        (image_id, "{}"),
    )
    conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
    assert conn.execute("SELECT COUNT(*) FROM image_raw_metadata").fetchone()[0] == 0


def test_content_hash_is_unique(conn):
    insert_image(conn)
    with pytest.raises(sqlite3.IntegrityError):
        insert_image(conn, file_path="y.png", file_name="y.png")
