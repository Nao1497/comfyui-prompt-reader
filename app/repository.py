"""All SQL for the application lives here (design.md §1 repository.py)."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.models import Image, Lora, LoraUsage, RawMetadata, ScanRun

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
            thumbnail_failed_count, renamed_count, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.started_at, run.finished_at, run.scanned_count, run.created_count,
            run.updated_count, run.missing_count, run.extract_failed_count,
            run.thumbnail_generated_count, run.thumbnail_failed_count, run.renamed_count,
            run.error,
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
    lora_id = filters.get("lora")
    if lora_id is not None:
        clauses.append("id IN (SELECT image_id FROM image_loras WHERE lora_id = ?)")
        params.append(lora_id)
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


# --- raw metadata -------------------------------------------------------------

def insert_raw_metadata(
    conn: sqlite3.Connection, image_id: int, prompt_json: str | None, workflow_json: str | None
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO image_raw_metadata (image_id, prompt_json, workflow_json) VALUES (?, ?, ?)",
        (image_id, prompt_json, workflow_json),
    )


def get_raw_metadata(conn: sqlite3.Connection, image_id: int) -> RawMetadata | None:
    row = conn.execute(
        "SELECT image_id, prompt_json, workflow_json FROM image_raw_metadata WHERE image_id = ?",
        (image_id,),
    ).fetchone()
    if row is None:
        return None
    return RawMetadata(image_id=row["image_id"], prompt_json=row["prompt_json"], workflow_json=row["workflow_json"])


# --- thumbnails ---------------------------------------------------------------

def set_thumbnail(
    conn: sqlite3.Connection, image_id: int, thumbnail_name: str | None, status: str, now: str
) -> None:
    conn.execute(
        "UPDATE images SET thumbnail_name = ?, thumbnail_status = ?, updated_at = ? WHERE id = ?",
        (thumbnail_name, status, now, image_id),
    )


# --- folders ------------------------------------------------------------------

def count_active_by_dir(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT dir_path, COUNT(*) AS n FROM images WHERE presence = 'active' GROUP BY dir_path"
    ).fetchall()
    return [(r["dir_path"], int(r["n"])) for r in rows]


def count_favorites_active(conn: sqlite3.Connection) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM images WHERE presence = 'active' AND is_favorite = 1"
    ).fetchone()[0])


def count_missing(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM images WHERE presence = 'missing'").fetchone()[0])


# --- loras --------------------------------------------------------------------

LORA_COLUMNS = [
    "id", "name", "file_name", "file_size", "file_mtime", "presence",
    "trigger_words", "memo", "created_at", "updated_at",
]
_LORA_SELECT = (
    "SELECT " + ", ".join("l." + c for c in LORA_COLUMNS)
    + ", (SELECT COUNT(*) FROM image_loras il JOIN images i ON i.id = il.image_id"
    + "    WHERE il.lora_id = l.id AND i.presence = 'active') AS image_count"
    + " FROM loras l"
)


def _row_to_lora(row: sqlite3.Row) -> Lora:
    data = {col: row[col] for col in LORA_COLUMNS}
    return Lora(**data, image_count=int(row["image_count"]))


def list_loras(conn: sqlite3.Connection) -> list[Lora]:
    rows = conn.execute(_LORA_SELECT + " ORDER BY l.name").fetchall()
    return [_row_to_lora(r) for r in rows]


def get_lora(conn: sqlite3.Connection, lora_id: int) -> Lora | None:
    row = conn.execute(_LORA_SELECT + " WHERE l.id = ?", (lora_id,)).fetchone()
    return _row_to_lora(row) if row else None


def find_lora_by_name(conn: sqlite3.Connection, name: str) -> Lora | None:
    row = conn.execute(_LORA_SELECT + " WHERE l.name = ?", (name,)).fetchone()
    return _row_to_lora(row) if row else None


def upsert_lora_by_name(conn: sqlite3.Connection, name: str, now: str) -> int:
    """Return the id for ``name``, inserting a presence='unknown' row when first seen."""
    existing = conn.execute("SELECT id FROM loras WHERE name = ?", (name,)).fetchone()
    if existing:
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO loras (name, file_name, file_size, file_mtime, presence, created_at, updated_at)
        VALUES (?, ?, NULL, NULL, 'unknown', ?, ?)
        """,
        (name, name.rsplit("/", 1)[-1], now, now),
    )
    return int(cur.lastrowid)


def set_lora_file(
    conn: sqlite3.Connection, lora_id: int, file_size: int, file_mtime: str, now: str
) -> None:
    conn.execute(
        "UPDATE loras SET file_size = ?, file_mtime = ?, presence = 'active', updated_at = ? WHERE id = ?",
        (file_size, file_mtime, now, lora_id),
    )


def mark_loras_missing_except(conn: sqlite3.Connection, seen_ids: set[int], now: str) -> int:
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS seen_lora_ids (id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM seen_lora_ids")
    conn.executemany("INSERT INTO seen_lora_ids (id) VALUES (?)", ((i,) for i in seen_ids))
    cur = conn.execute(
        """
        UPDATE loras SET presence = 'missing', updated_at = ?
         WHERE presence = 'active' AND id NOT IN (SELECT id FROM seen_lora_ids)
        """,
        (now,),
    )
    conn.execute("DELETE FROM seen_lora_ids")
    return int(cur.rowcount)


def update_lora_notes(
    conn: sqlite3.Connection, lora_id: int, trigger_words: str | None, memo: str | None, now: str
) -> bool:
    sets = ["updated_at = ?"]
    params: list[Any] = [now]
    if trigger_words is not None:
        sets.append("trigger_words = ?")
        params.append(trigger_words)
    if memo is not None:
        sets.append("memo = ?")
        params.append(memo)
    params.append(lora_id)
    cur = conn.execute(f"UPDATE loras SET {', '.join(sets)} WHERE id = ?", params)
    return cur.rowcount == 1


def replace_image_loras(
    conn: sqlite3.Connection, image_id: int, usages: list[LoraUsage], now: str
) -> None:
    conn.execute("DELETE FROM image_loras WHERE image_id = ?", (image_id,))
    for usage in usages:
        lora_id = upsert_lora_by_name(conn, usage.name, now)
        conn.execute(
            """
            INSERT OR REPLACE INTO image_loras (image_id, lora_id, strength_model, strength_clip)
            VALUES (?, ?, ?, ?)
            """,
            (image_id, lora_id, usage.strength_model, usage.strength_clip),
        )


def get_image_loras(conn: sqlite3.Connection, image_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT l.id, l.name, l.presence, l.trigger_words, il.strength_model, il.strength_clip
          FROM image_loras il JOIN loras l ON l.id = il.lora_id
         WHERE il.image_id = ?
         ORDER BY l.name
        """,
        (image_id,),
    ).fetchall()


def iter_raw_prompts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT image_id, prompt_json FROM image_raw_metadata WHERE prompt_json IS NOT NULL"
    ).fetchall()


# --- prompt tokens (FR-48) ----------------------------------------------------

def replace_prompt_tokens(
    conn: sqlite3.Connection, image_id: int, positive: list[str], negative: list[str]
) -> None:
    """Store the normalised words of both prompts, in order. Unknown words are kept too."""
    conn.execute("DELETE FROM image_prompt_tokens WHERE image_id = ?", (image_id,))
    rows = [(image_id, "positive", i, t) for i, t in enumerate(positive)]
    rows += [(image_id, "negative", i, t) for i, t in enumerate(negative)]
    conn.executemany(
        "INSERT INTO image_prompt_tokens (image_id, side, position, token) VALUES (?, ?, ?, ?)", rows
    )


def get_prompt_tokens(conn: sqlite3.Connection, image_id: int, side: str = "positive") -> list[str]:
    rows = conn.execute(
        "SELECT token FROM image_prompt_tokens WHERE image_id = ? AND side = ? ORDER BY position",
        (image_id, side),
    ).fetchall()
    return [r["token"] for r in rows]


def image_ids_without_tokens(conn: sqlite3.Connection) -> list[tuple[int, str | None, str | None]]:
    """Images that have a prompt but no stored tokens (registered before FR-48)."""
    rows = conn.execute(
        """
        SELECT id, positive_prompt, negative_prompt FROM images
         WHERE (positive_prompt IS NOT NULL OR negative_prompt IS NOT NULL)
           AND NOT EXISTS (SELECT 1 FROM image_prompt_tokens t WHERE t.image_id = images.id)
        """
    ).fetchall()
    return [(int(r["id"]), r["positive_prompt"], r["negative_prompt"]) for r in rows]
