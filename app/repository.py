"""All SQL for the application lives here (design.md §1 repository.py)."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.models import Image, ScanRun

IMAGE_COLUMNS = [
    "id", "content_hash", "file_path", "dir_path", "file_name", "file_size",
    "image_width", "image_height", "file_mtime", "presence", "is_favorite",
    "thumbnail_name", "thumbnail_status", "extraction_status",
    "positive_prompt", "negative_prompt", "model_name", "seed", "steps", "cfg",
    "sampler_name", "scheduler", "gen_width", "gen_height",
    "created_at", "updated_at",
]
_IMAGE_SELECT = "SELECT " + ", ".join(IMAGE_COLUMNS) + " FROM images"


def _row_to_image(row: sqlite3.Row) -> Image:
    data = {col: row[col] for col in IMAGE_COLUMNS}
    data["is_favorite"] = bool(data["is_favorite"])
    return Image(**data)


def _row_to_scan_run(row: sqlite3.Row) -> ScanRun:
    return ScanRun(**{k: row[k] for k in row.keys()})


# --- images -------------------------------------------------------------------

def find_image_by_hash(conn: sqlite3.Connection, content_hash: str) -> Image | None:
    row = conn.execute(_IMAGE_SELECT + " WHERE content_hash = ?", (content_hash,)).fetchone()
    return _row_to_image(row) if row else None


def get_image(conn: sqlite3.Connection, image_id: int) -> Image | None:
    row = conn.execute(_IMAGE_SELECT + " WHERE id = ?", (image_id,)).fetchone()
    return _row_to_image(row) if row else None


def insert_image(conn: sqlite3.Connection, values: dict[str, Any]) -> int:
    """Insert a row from a column->value mapping and return the new id."""
    cols = ", ".join(values)
    params = ", ".join(f":{c}" for c in values)
    cur = conn.execute(f"INSERT INTO images ({cols}) VALUES ({params})", values)
    return int(cur.lastrowid)


def set_presence(conn: sqlite3.Connection, image_id: int, presence: str, now: str) -> None:
    conn.execute(
        "UPDATE images SET presence = ?, updated_at = ? WHERE id = ?",
        (presence, now, image_id),
    )


def count_images(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM images").fetchone()[0])


def list_all_images(conn: sqlite3.Connection) -> list[Image]:
    rows = conn.execute(_IMAGE_SELECT + " ORDER BY id").fetchall()
    return [_row_to_image(r) for r in rows]


# --- scan_runs ----------------------------------------------------------------

def insert_scan_run(conn: sqlite3.Connection, run: ScanRun) -> int:
    cur = conn.execute(
        """
        INSERT INTO scan_runs (
            started_at, finished_at, scanned_count, created_count, updated_count,
            missing_count, extract_failed_count, thumbnail_generated_count,
            thumbnail_failed_count, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.started_at, run.finished_at, run.scanned_count, run.created_count,
            run.updated_count, run.missing_count, run.extract_failed_count,
            run.thumbnail_generated_count, run.thumbnail_failed_count, run.error,
        ),
    )
    return int(cur.lastrowid)


def get_scan_run(conn: sqlite3.Connection, run_id: int) -> ScanRun | None:
    row = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_scan_run(row) if row else None
