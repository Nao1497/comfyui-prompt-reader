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


# --- listing ------------------------------------------------------------------

LIST_COLUMNS = [
    "id", "file_name", "file_path", "dir_path", "file_size", "image_width",
    "image_height", "file_mtime", "presence", "is_favorite", "thumbnail_status",
    "extraction_status",
]


def _list_where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    """Build the WHERE clause shared by list_images and count_images_filtered."""
    clauses: list[str] = []
    params: list[Any] = []
    if filters.get("favorite_only"):
        clauses.append("is_favorite = 1")
    if filters.get("missing_only"):
        clauses.append("presence = 'missing'")
    elif not filters.get("include_missing"):
        clauses.append("presence = 'active'")
    dir_path = filters.get("dir")
    recursive = filters.get("recursive", True)
    if dir_path is not None:
        # 確認事項 #8: dir='' with recursive -> no folder condition.
        if not recursive:
            clauses.append("dir_path = ?")
            params.append(dir_path)
        elif dir_path != "":
            clauses.append("(dir_path = ? OR dir_path LIKE ? ESCAPE '\\')")
            params.append(dir_path)
            params.append(_like_prefix(dir_path) + "/%")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def _like_prefix(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_images(
    conn: sqlite3.Connection,
    limit: int,
    cursor: tuple[str, int] | None = None,
    **filters: Any,
) -> list[sqlite3.Row]:
    where, params = _list_where(filters)
    if cursor is not None:
        where += (" AND " if where else " WHERE ") + "(file_mtime, id) < (?, ?)"
        params.extend(cursor)
    sql = (
        "SELECT " + ", ".join(LIST_COLUMNS) + " FROM images" + where
        + " ORDER BY file_mtime DESC, id DESC LIMIT ?"
    )
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def count_images_filtered(conn: sqlite3.Connection, **filters: Any) -> int:
    where, params = _list_where(filters)
    return int(conn.execute("SELECT COUNT(*) FROM images" + where, params).fetchone()[0])


# --- rescan support -----------------------------------------------------------

def update_image_path(
    conn: sqlite3.Connection,
    image_id: int,
    file_path: str,
    dir_path: str,
    file_name: str,
    file_mtime: str,
    now: str,
) -> None:
    """FR-4: same content found at a new path. Favorite/metadata/thumbnail untouched."""
    conn.execute(
        """
        UPDATE images
           SET file_path = ?, dir_path = ?, file_name = ?, file_mtime = ?,
               presence = 'active', updated_at = ?
         WHERE id = ?
        """,
        (file_path, dir_path, file_name, file_mtime, now, image_id),
    )


def mark_missing_except(conn: sqlite3.Connection, seen_ids: set[int], now: str) -> int:
    """FR-5: set presence='missing' on active rows not in ``seen_ids``; return count."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS seen_ids (id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM seen_ids")
    conn.executemany("INSERT INTO seen_ids (id) VALUES (?)", ((i,) for i in seen_ids))
    cur = conn.execute(
        """
        UPDATE images SET presence = 'missing', updated_at = ?
         WHERE presence = 'active' AND id NOT IN (SELECT id FROM seen_ids)
        """,
        (now,),
    )
    conn.execute("DELETE FROM seen_ids")
    return int(cur.rowcount)


def set_favorite(conn: sqlite3.Connection, image_id: int, is_favorite: bool, now: str) -> bool:
    cur = conn.execute(
        "UPDATE images SET is_favorite = ?, updated_at = ? WHERE id = ?",
        (1 if is_favorite else 0, now, image_id),
    )
    return cur.rowcount == 1
