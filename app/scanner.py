"""Folder scan: enumerate PNGs, hash them, register/refresh records (design.md §3)."""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image as PILImage

from app import repository
from app.config import AppConfig
from app.models import ScanRun

log = logging.getLogger(__name__)

HASH_CHUNK_SIZE = 64 * 1024


class ScanRootNotFound(Exception):
    """scan_root does not exist; nothing was modified (FR-24)."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mtime_to_iso(ts: float) -> str:
    # 確認事項 #18: second precision, UTC.
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_png_files(scan_root: Path, thumbnail_dir_name: str) -> list[Path]:
    """Recursively list ``*.png`` (any case) under ``scan_root``.

    Skips the thumbnail directory and any directory whose name starts with ``.``
    (FR-32 / design §3 step 3). The result is sorted so scans are deterministic.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(scan_root):
        dirnames[:] = sorted(
            d for d in dirnames if d != thumbnail_dir_name and not d.startswith(".")
        )
        for name in filenames:
            if name.lower().endswith(".png"):
                found.append(Path(dirpath) / name)
    found.sort()
    return found


def relative_parts(scan_root: Path, path: Path) -> tuple[str, str, str]:
    """Return (file_path, dir_path, file_name) relative to scan_root with ``/`` separators."""
    rel = path.relative_to(scan_root)
    file_path = rel.as_posix()
    dir_path = rel.parent.as_posix()
    if dir_path == ".":
        dir_path = ""
    return file_path, dir_path, rel.name


def read_image_size(path: Path) -> tuple[int, int] | None:
    """Return (width, height) or None when Pillow cannot open the file."""
    try:
        with PILImage.open(path) as img:
            return img.size
    except Exception as exc:  # noqa: BLE001 - any unreadable file is "broken" here
        log.warning("cannot open %s: %s", path, exc)
        return None


def run_scan(config: AppConfig, conn: sqlite3.Connection) -> ScanRun:
    """Execute one scan of ``config.scan_root`` and return the recorded ScanRun."""
    scan_root = config.scan_root
    if not scan_root.is_dir():
        raise ScanRootNotFound(f"scan root does not exist: {scan_root}")

    config.thumbnail_dir.mkdir(exist_ok=True)

    run = ScanRun(id=0, started_at=utc_now(), finished_at=None)
    seen_hashes: set[str] = set()
    seen_ids: set[int] = set()

    for path in iter_png_files(scan_root, config.thumbnail_dir_name):
        run.scanned_count += 1
        try:
            content_hash = sha256_of_file(path)
        except OSError as exc:
            # 確認事項 #7: unreadable file -> counted as scanned, not registered.
            log.warning("cannot read %s: %s", path, exc)
            continue

        if content_hash in seen_hashes:
            # 確認事項 #6: first occurrence within a run wins.
            continue
        seen_hashes.add(content_hash)

        now = utc_now()
        existing = repository.find_image_by_hash(conn, content_hash)
        file_path, dir_path, file_name = relative_parts(scan_root, path)

        if existing is None:
            image_id = _register_new(
                conn, config, path, content_hash, file_path, dir_path, file_name, now, run
            )
        elif existing.file_path == file_path:
            image_id = existing.id
            repository.set_presence(conn, image_id, "active", now)
        else:
            # FR-4: same content at a new path. 確認事項 #5: dir_path is updated too.
            image_id = existing.id
            repository.update_image_path(
                conn, image_id, file_path, dir_path, file_name,
                mtime_to_iso(path.stat().st_mtime), now,
            )
            run.updated_count += 1
        seen_ids.add(image_id)
        conn.commit()

    # FR-5: anything active that did not show up this run is now missing.
    # 確認事項 #4: missing_count = rows newly transitioned in this run.
    run.missing_count = repository.mark_missing_except(conn, seen_ids, utc_now())
    run.finished_at = utc_now()
    run.id = repository.insert_scan_run(conn, run)
    conn.commit()
    return run


def _register_new(
    conn: sqlite3.Connection,
    config: AppConfig,
    path: Path,
    content_hash: str,
    file_path: str,
    dir_path: str,
    file_name: str,
    now: str,
    run: ScanRun,
) -> int:
    stat = path.stat()
    size = read_image_size(path)
    values = {
        "content_hash": content_hash,
        "file_path": file_path,
        "dir_path": dir_path,
        "file_name": file_name,
        "file_size": stat.st_size,
        "image_width": size[0] if size else None,
        "image_height": size[1] if size else None,
        "file_mtime": mtime_to_iso(stat.st_mtime),
        "presence": "active",
        "is_favorite": 0,
        "thumbnail_name": None,
        # 確認事項 #7: a PNG Pillow cannot open is registered with thumbnail failed.
        "thumbnail_status": "pending" if size else "failed",
        "extraction_status": "none",
        "created_at": now,
        "updated_at": now,
    }
    image_id = repository.insert_image(conn, values)
    run.created_count += 1
    return image_id
