"""SQLite connection and schema initialisation (design.md §2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash      TEXT    NOT NULL UNIQUE,
    file_path         TEXT    NOT NULL,
    dir_path          TEXT    NOT NULL,
    file_name         TEXT    NOT NULL,
    file_size         INTEGER NOT NULL,
    image_width       INTEGER,
    image_height      INTEGER,
    file_mtime        TEXT    NOT NULL,
    presence          TEXT    NOT NULL,
    is_favorite       INTEGER NOT NULL DEFAULT 0,
    thumbnail_name    TEXT,
    thumbnail_status  TEXT    NOT NULL,
    extraction_status TEXT    NOT NULL,
    positive_prompt   TEXT,
    negative_prompt   TEXT,
    model_name        TEXT,
    seed              INTEGER,
    steps             INTEGER,
    cfg               REAL,
    sampler_name      TEXT,
    scheduler         TEXT,
    gen_width         INTEGER,
    gen_height        INTEGER,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_images_content_hash ON images (content_hash);
CREATE INDEX IF NOT EXISTS idx_images_cursor   ON images (file_mtime DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_images_favorite ON images (is_favorite, file_mtime DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_images_dir      ON images (dir_path, file_mtime DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_images_presence ON images (presence);

CREATE TABLE IF NOT EXISTS image_raw_metadata (
    image_id      INTEGER PRIMARY KEY REFERENCES images (id) ON DELETE CASCADE,
    prompt_json   TEXT,
    workflow_json TEXT
);

CREATE TABLE IF NOT EXISTS loras (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,   -- ComfyUI lora_name: path relative to the LoRA folder, "/" separators
    file_name     TEXT    NOT NULL,
    file_size     INTEGER,
    file_mtime    TEXT,
    presence      TEXT    NOT NULL,          -- active / missing / unknown (seen only in workflows)
    trigger_words TEXT    NOT NULL DEFAULT '',
    memo          TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS image_loras (
    image_id       INTEGER NOT NULL REFERENCES images (id) ON DELETE CASCADE,
    lora_id        INTEGER NOT NULL REFERENCES loras (id) ON DELETE CASCADE,
    strength_model REAL,
    strength_clip  REAL,
    PRIMARY KEY (image_id, lora_id)
);

CREATE INDEX IF NOT EXISTS idx_image_loras_lora ON image_loras (lora_id);

CREATE TABLE IF NOT EXISTS scan_runs (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at                TEXT    NOT NULL,
    finished_at               TEXT,
    scanned_count             INTEGER NOT NULL DEFAULT 0,
    created_count             INTEGER NOT NULL DEFAULT 0,
    updated_count             INTEGER NOT NULL DEFAULT 0,
    missing_count             INTEGER NOT NULL DEFAULT 0,
    extract_failed_count      INTEGER NOT NULL DEFAULT 0,
    thumbnail_generated_count INTEGER NOT NULL DEFAULT 0,
    thumbnail_failed_count    INTEGER NOT NULL DEFAULT 0,
    renamed_count             INTEGER NOT NULL DEFAULT 0,
    error                     TEXT
);
"""

# Columns added after the first release; applied to existing databases on start-up.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("scan_runs", "renamed_count", "INTEGER NOT NULL DEFAULT 0"),
]


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open ``db_path`` (creating parent directories) with WAL and foreign keys on.

    ``check_same_thread=False`` lets the FastAPI threadpool share the connection;
    callers serialise access with a lock (NFR-1: single local user).
    """
    db_path = Path(db_path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they do not exist, then add any missing columns."""
    conn.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()


def open_database(db_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    init_schema(conn)
    return conn
