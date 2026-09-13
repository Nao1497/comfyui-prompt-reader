"""Tag dictionary import from a danbooru CSV and image linking (design.md §6, FR-45/47/48)."""

from __future__ import annotations

import csv
import logging
import sqlite3
from dataclasses import dataclass
from typing import IO

from app import db, prompt_tokens, repository, scanner
from app.models import TagImport

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = (
    "tag", "category", "category_name", "post_count", "created_at",
    "aliases", "other_names", "posts_url", "wiki_url", "has_wiki",
)
MAX_TAG_CSV_BYTES = 512 * 1024 * 1024
BATCH_SIZE = 5000


class InvalidTagCsv(Exception):
    """The uploaded file is not a tag CSV with the expected header."""


@dataclass
class _Counters:
    read: int = 0
    imported: int = 0
    skipped: int = 0


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def run_import(conn: sqlite3.Connection, stream: IO[str], file_name: str | None) -> TagImport:
    """Replace every ``source='csv'`` tag with the rows of ``stream``; keep LoRA-sourced rows.

    Runs as one transaction. Raises :class:`InvalidTagCsv` before touching the
    dictionary when the header is not the expected one.
    """
    reader = csv.DictReader(stream)
    fields = [f.strip() for f in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in fields]
    if missing:
        raise InvalidTagCsv(f"required column is missing: {', '.join(missing)}")

    started = scanner.utc_now()
    now = started
    counters = _Counters()
    error: str | None = None
    try:
        # Rows registered from LoRA trigger words survive; a CSV row with the same
        # normalised name takes over that row (keeping lora_id).
        lora_rows = {
            r["name_normalized"]: int(r["id"])
            for r in conn.execute("SELECT id, name_normalized FROM tags WHERE source = 'lora'")
        }
        conn.execute("DELETE FROM tags WHERE source = 'csv'")

        batch: list[tuple] = []
        alias_pairs: list[tuple[str, str, str]] = []

        def flush() -> None:
            if not batch:
                return
            cur = conn.executemany(
                """
                INSERT OR IGNORE INTO tags (
                    name, name_normalized, category, category_name, post_count, tag_created_at,
                    aliases, other_names, posts_url, wiki_url, has_wiki, source, lora_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'csv', NULL, ?, ?)
                """,
                batch,
            )
            counters.imported += cur.rowcount
            counters.skipped += len(batch) - cur.rowcount  # duplicates within the CSV
            batch.clear()

        for row in reader:
            counters.read += 1
            name = (row.get("tag") or "").strip()
            category = _parse_int(row.get("category"))
            post_count = _parse_int(row.get("post_count"))
            if not name or category is None or post_count is None:
                counters.skipped += 1
                continue
            nn = prompt_tokens.normalize_name(name)
            if not nn:
                counters.skipped += 1
                continue
            values = (
                name, nn, category, (row.get("category_name") or "").strip(), post_count,
                (row.get("created_at") or "").strip() or None,
                (row.get("aliases") or "").strip(), (row.get("other_names") or "").strip(),
                (row.get("posts_url") or "").strip() or None, (row.get("wiki_url") or "").strip() or None,
                (row.get("has_wiki") or "").strip() or None,
            )
            for alias in values[6].split(","):
                a = prompt_tokens.normalize_name(alias)
                if a and a != nn:
                    alias_pairs.append((a, name, a))
            lora_id = lora_rows.pop(nn, None)
            if lora_id is not None:
                conn.execute(
                    """
                    UPDATE tags SET name = ?, category = ?, category_name = ?, post_count = ?,
                        tag_created_at = ?, aliases = ?, other_names = ?, posts_url = ?, wiki_url = ?,
                        has_wiki = ?, source = 'csv', updated_at = ?
                     WHERE id = ?
                    """,
                    (values[0], values[2], values[3], values[4], values[5], values[6], values[7],
                     values[8], values[9], values[10], now, lora_id),
                )
                counters.imported += 1
                continue
            batch.append(values + (now, now))
            if len(batch) >= BATCH_SIZE:
                flush()
        flush()

        # Aliases resolve to the canonical tag; an alias that is itself a canonical
        # name is never stored, so canonical names always win (FR-47).
        conn.executemany(
            """
            INSERT OR IGNORE INTO tag_aliases (alias_normalized, tag_id)
            SELECT ?, id FROM tags WHERE name = ?
               AND NOT EXISTS (SELECT 1 FROM tags t2 WHERE t2.name_normalized = ?)
            """,
            alias_pairs,
        )
        alias_count = int(conn.execute("SELECT COUNT(*) FROM tag_aliases").fetchone()[0])

        repository.rebuild_fts(conn)
        ensure_prompt_tokens(conn)
        linked = repository.rebuild_image_tags(conn)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - record the failure, keep the old dictionary
        conn.rollback()
        error = str(exc)
        log.exception("tag import failed")
        run = TagImport(id=0, started_at=started, finished_at=scanner.utc_now(), file_name=file_name,
                        read_count=counters.read, error=error)
        run.id = repository.insert_tag_import(conn, run)
        conn.commit()
        raise

    run = TagImport(
        id=0, started_at=started, finished_at=scanner.utc_now(), file_name=file_name,
        read_count=counters.read, imported_count=counters.imported, skipped_count=counters.skipped,
        alias_count=alias_count, linked_image_count=linked,
    )
    run.id = repository.insert_tag_import(conn, run)
    conn.commit()
    return run


def ensure_prompt_tokens(conn: sqlite3.Connection) -> int:
    """Tokenise prompts of images registered before FR-48 so they can be linked."""
    pending = repository.image_ids_without_tokens(conn)
    for image_id, positive, negative in pending:
        scanner.store_prompt_tokens(conn, image_id, positive, negative)
    return len(pending)
