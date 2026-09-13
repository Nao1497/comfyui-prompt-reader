"""Tag dictionary import from a danbooru CSV and image linking (design.md §6, FR-45/47/48)."""

from __future__ import annotations

import csv
import logging
import sqlite3
import time
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

    Rows are staged in an index-free temporary table, then inserted sorted by the
    matching key so the unique indexes are built in order (design §6). Runs as one
    transaction. Raises :class:`InvalidTagCsv` before touching the dictionary when
    the header is not the expected one.
    """
    reader = csv.DictReader(stream)
    fields = [f.strip() for f in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in fields]
    if missing:
        raise InvalidTagCsv(f"required column is missing: {', '.join(missing)}")

    started = scanner.utc_now()
    now = started
    counters = _Counters()
    timer = _PhaseTimer()
    try:
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(
            """
            DROP TABLE IF EXISTS temp.tag_stage;
            DROP TABLE IF EXISTS temp.alias_stage;
            CREATE TEMP TABLE tag_stage (
                name TEXT, name_normalized TEXT, category INTEGER, category_name TEXT, post_count INTEGER,
                tag_created_at TEXT, aliases TEXT, other_names TEXT, posts_url TEXT, wiki_url TEXT, has_wiki TEXT
            );
            CREATE TEMP TABLE alias_stage (alias_normalized TEXT, tag_name TEXT);
            """
        )
        conn.execute("BEGIN")

        # --- stage ------------------------------------------------------------
        batch: list[tuple] = []
        alias_batch: list[tuple[str, str]] = []

        def flush() -> None:
            if batch:
                conn.executemany("INSERT INTO tag_stage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
                batch.clear()
            if alias_batch:
                conn.executemany("INSERT INTO alias_stage VALUES (?, ?)", alias_batch)
                alias_batch.clear()

        for row in reader:
            counters.read += 1
            name = (row.get("tag") or "").strip()
            category = _parse_int(row.get("category"))
            post_count = _parse_int(row.get("post_count"))
            nn = prompt_tokens.normalize_name(name) if name else ""
            if not nn or category is None or post_count is None:
                counters.skipped += 1
                continue
            aliases = (row.get("aliases") or "").strip()
            batch.append((
                name, nn, category, (row.get("category_name") or "").strip(), post_count,
                (row.get("created_at") or "").strip() or None, aliases,
                (row.get("other_names") or "").strip(),
                (row.get("posts_url") or "").strip() or None, (row.get("wiki_url") or "").strip() or None,
                (row.get("has_wiki") or "").strip() or None,
            ))
            if aliases:
                for alias in aliases.split(","):
                    a = prompt_tokens.normalize_name(alias)
                    if a and a != nn:
                        alias_batch.append((a, name))
            if len(batch) >= BATCH_SIZE:
                flush()
        flush()
        staged = int(conn.execute("SELECT COUNT(*) FROM tag_stage").fetchone()[0])
        timer.mark("stage")

        # --- replace CSV rows -------------------------------------------------
        # Remove child rows as sets first; per-row FK cascades over a million
        # parents are far slower than these three statements.
        conn.execute("DELETE FROM tag_aliases WHERE tag_id IN (SELECT id FROM tags WHERE source = 'csv')")
        conn.execute("DELETE FROM image_tags WHERE tag_id IN (SELECT id FROM tags WHERE source = 'csv')")
        conn.execute("DELETE FROM tags WHERE source = 'csv'")
        timer.mark("delete")
        # A CSV row with the same normalised name as a LoRA trigger word takes that row over.
        cur = conn.execute(
            """
            UPDATE tags SET name = s.name, category = s.category, category_name = s.category_name,
                post_count = s.post_count, tag_created_at = s.tag_created_at, aliases = s.aliases,
                other_names = s.other_names, posts_url = s.posts_url, wiki_url = s.wiki_url,
                has_wiki = s.has_wiki, source = 'csv', updated_at = ?
              FROM tag_stage AS s
             WHERE tags.name_normalized = s.name_normalized AND tags.source = 'lora'
            """,
            (now,),
        )
        taken_over = int(cur.rowcount)
        conn.execute(
            "DELETE FROM tag_stage WHERE name_normalized IN (SELECT name_normalized FROM tags)"
        )
        for idx in ("idx_tags_post_count", "idx_tags_source"):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO tags (
                name, name_normalized, category, category_name, post_count, tag_created_at,
                aliases, other_names, posts_url, wiki_url, has_wiki, source, lora_id, created_at, updated_at
            )
            SELECT name, name_normalized, category, category_name, post_count, tag_created_at,
                   aliases, other_names, posts_url, wiki_url, has_wiki, 'csv', NULL, ?, ?
              FROM tag_stage ORDER BY name_normalized
            """,
            (now, now),
        )
        inserted = int(cur.rowcount)
        counters.imported = inserted + taken_over
        counters.skipped += staged - inserted - taken_over  # duplicates within the CSV
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_post_count ON tags (post_count DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_source ON tags (source)")
        timer.mark("insert")

        # --- aliases (FR-47): a canonical name is never also an alias ----------
        conn.execute(
            """
            INSERT OR IGNORE INTO tag_aliases (alias_normalized, tag_id)
            SELECT a.alias_normalized, t.id
              FROM alias_stage a JOIN tags t ON t.name = a.tag_name
             WHERE NOT EXISTS (SELECT 1 FROM tags t2 WHERE t2.name_normalized = a.alias_normalized)
            """
        )
        alias_count = int(conn.execute("SELECT COUNT(*) FROM tag_aliases").fetchone()[0])
        timer.mark("aliases")

        repository.rebuild_fts(conn)
        timer.mark("fts")
        ensure_prompt_tokens(conn)
        linked = repository.rebuild_image_tags(conn)
        timer.mark("link")
        conn.execute("DROP TABLE IF EXISTS temp.tag_stage")
        conn.execute("DROP TABLE IF EXISTS temp.alias_stage")
        conn.commit()
        timer.mark("commit")
    except Exception as exc:  # noqa: BLE001 - record the failure, keep the old dictionary
        conn.rollback()
        log.exception("tag import failed")
        run = TagImport(id=0, started_at=started, finished_at=scanner.utc_now(), file_name=file_name,
                        read_count=counters.read, error=str(exc))
        run.id = repository.insert_tag_import(conn, run)
        conn.commit()
        raise
    finally:
        conn.execute("PRAGMA synchronous = FULL")

    log.info("tag import: %s rows, %s", counters.read, timer.summary())
    run = TagImport(
        id=0, started_at=started, finished_at=scanner.utc_now(), file_name=file_name,
        read_count=counters.read, imported_count=counters.imported, skipped_count=counters.skipped,
        alias_count=alias_count, linked_image_count=linked,
    )
    run.id = repository.insert_tag_import(conn, run)
    conn.commit()
    return run


class _PhaseTimer:
    def __init__(self) -> None:
        self._t = time.perf_counter()
        self.phases: list[tuple[str, float]] = []

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.phases.append((name, now - self._t))
        self._t = now

    def summary(self) -> str:
        return ", ".join(f"{n}={s:.1f}s" for n, s in self.phases)


def ensure_prompt_tokens(conn: sqlite3.Connection) -> int:
    """Tokenise prompts of images registered before FR-48 so they can be linked."""
    pending = repository.image_ids_without_tokens(conn)
    for image_id, positive, negative in pending:
        scanner.store_prompt_tokens(conn, image_id, positive, negative)
    return len(pending)
