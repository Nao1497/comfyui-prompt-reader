"""Folder scan: enumerate PNGs, hash them, register/refresh records (design.md §3)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image as PILImage

from app import comfy_metadata, prompt_tokens, repository, thumbnailer
from app.config import AppConfig
from app.models import Image, ScanRun

log = logging.getLogger(__name__)

HASH_CHUNK_SIZE = 64 * 1024

# FR-40 naming rule: <file mtime, local time>_<uuid4 first 8 hex>.png
CANONICAL_NAME_RE = re.compile(r"^\d{8}T\d{6}_[0-9a-f]{8}\.png$")


class ScanRootNotFound(Exception):
    """scan_root does not exist; nothing was modified (FR-24)."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mtime_to_iso(ts: float) -> str:
    # 確認事項 #18: second precision, UTC.
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_canonical_name(name: str) -> bool:
    return CANONICAL_NAME_RE.match(name) is not None


def canonical_name_for(path: Path) -> str:
    stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}_{uuid.uuid4().hex[:8]}.png"


def rename_to_canonical(path: Path) -> Path:
    """Rename ``path`` in place to the FR-40 pattern; return the (possibly unchanged) path.

    Only the name changes: same directory, same bytes, and ``rename`` keeps mtime.
    """
    if is_canonical_name(path.name):
        return path
    for _ in range(8):
        target = path.with_name(canonical_name_for(path))
        if target.exists():
            continue
        path.rename(target)
        return target
    raise OSError(f"could not find a free canonical name for {path}")


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


@dataclass
class ImageInfo:
    width: int
    height: int
    prompt_text: str | None   # raw tEXt chunk "prompt"
    workflow_text: str | None  # raw tEXt chunk "workflow"


def read_image_info(path: Path) -> ImageInfo | None:
    """Return size and raw ComfyUI tEXt chunks, or None when Pillow cannot open the file."""
    try:
        with PILImage.open(path) as img:
            info = img.info
            return ImageInfo(
                width=img.size[0],
                height=img.size[1],
                prompt_text=_text_chunk(info.get("prompt")),
                workflow_text=_text_chunk(info.get("workflow")),
            )
    except Exception as exc:  # noqa: BLE001 - any unreadable file is "broken" here
        log.warning("cannot open %s: %s", path, exc)
        return None


def _text_chunk(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def parse_prompt(prompt_text: str | None) -> dict | None:
    """Parse the ``prompt`` chunk; None when absent or not a JSON object."""
    if prompt_text is None:
        return None
    try:
        prompt = json.loads(prompt_text)
    except ValueError:
        return None
    return prompt if isinstance(prompt, dict) else None


def extract_metadata_columns(info: ImageInfo) -> tuple[dict, str, dict | None]:
    """Map raw chunks to image columns; return (columns, extraction_status, parsed prompt)."""
    if info.prompt_text is None and info.workflow_text is None:
        return {}, "none", None
    prompt = parse_prompt(info.prompt_text)
    if prompt is not None:
        try:
            meta = comfy_metadata.extract(prompt)
        except Exception as exc:  # noqa: BLE001 - never let a weird graph stop the scan
            log.warning("prompt metadata could not be analysed: %s", exc)
            meta = comfy_metadata.ExtractedMetadata().finalize()
    else:
        # 確認事項 #16: workflow only, or unparsable prompt -> partial with nothing extracted
        meta = comfy_metadata.ExtractedMetadata().finalize()
    columns = {
        "positive_prompt": meta.positive_prompt,
        "negative_prompt": meta.negative_prompt,
        "model_name": meta.model_name,
        "seed": meta.seed,
        "steps": meta.steps,
        "cfg": meta.cfg,
        "sampler_name": meta.sampler_name,
        "scheduler": meta.scheduler,
        "gen_width": meta.gen_width,
        "gen_height": meta.gen_height,
    }
    return columns, meta.status, prompt


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
        if config.rename_on_scan and not is_canonical_name(path.name):
            try:
                path = rename_to_canonical(path)
                run.renamed_count += 1
            except OSError as exc:
                log.warning("cannot rename %s: %s", path, exc)
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
        else:
            image_id = existing.id
            if existing.file_path == file_path:
                repository.set_presence(conn, image_id, "active", now)
            else:
                # FR-4: same content at a new path. 確認事項 #5: dir_path is updated too.
                repository.update_image_path(
                    conn, image_id, file_path, dir_path, file_name,
                    mtime_to_iso(path.stat().st_mtime), now,
                )
                run.updated_count += 1
            if _needs_thumbnail(existing):
                # FR-29: retry only failed ones (確認事項 #5: regardless of path change).
                _make_thumbnail(conn, config, path, image_id, run)
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
    info = read_image_info(path)
    columns, extraction_status, prompt = extract_metadata_columns(info) if info else ({}, "none", None)
    values = {
        "content_hash": content_hash,
        "file_path": file_path,
        "dir_path": dir_path,
        "file_name": file_name,
        "file_size": stat.st_size,
        "image_width": info.width if info else None,
        "image_height": info.height if info else None,
        "file_mtime": mtime_to_iso(stat.st_mtime),
        "presence": "active",
        "is_favorite": 0,
        "thumbnail_name": None,
        # 確認事項 #7: a PNG Pillow cannot open is registered with thumbnail failed.
        "thumbnail_status": "pending" if info else "failed",
        "extraction_status": extraction_status,
        **columns,
        "created_at": now,
        "updated_at": now,
    }
    image_id = repository.insert_image(conn, values)
    run.created_count += 1
    if info and (info.prompt_text is not None or info.workflow_text is not None):
        repository.insert_raw_metadata(conn, image_id, info.prompt_text, info.workflow_text)
    if prompt is not None:
        record_lora_usage(conn, image_id, prompt, now)
    store_prompt_tokens(conn, image_id, columns.get("positive_prompt"), columns.get("negative_prompt"))
    if extraction_status == "partial":
        # 確認事項 #3: extract_failed_count = newly registered images left partial.
        run.extract_failed_count += 1
    if info is None:
        run.thumbnail_failed_count += 1
    else:
        _make_thumbnail(conn, config, path, image_id, run)
    return image_id


def store_prompt_tokens(
    conn: sqlite3.Connection, image_id: int, positive: str | None, negative: str | None
) -> None:
    """FR-48: keep the normalised words of both prompts so the dictionary can be joined later."""
    pos = prompt_tokens.tokenize(positive)
    neg = prompt_tokens.tokenize(negative)
    if pos or neg:
        repository.replace_prompt_tokens(conn, image_id, pos, neg)


def record_lora_usage(conn: sqlite3.Connection, image_id: int, prompt: dict, now: str) -> int:
    """Link the image to every LoRA its workflow references; returns the number of links."""
    try:
        usages = comfy_metadata.extract_loras(prompt)
    except Exception as exc:  # noqa: BLE001
        log.warning("lora extraction failed for image %s: %s", image_id, exc)
        return 0
    repository.replace_image_loras(conn, image_id, usages, now)
    return len(usages)


def _needs_thumbnail(image: Image) -> bool:
    return image.thumbnail_name is None or image.thumbnail_status != "ok"


def _make_thumbnail(
    conn: sqlite3.Connection, config: AppConfig, path: Path, image_id: int, run: ScanRun
) -> None:
    """FR-25/28: generate a thumbnail; a failure is recorded and never stops the scan."""
    try:
        name = thumbnailer.generate_thumbnail(
            path, config.thumbnail_dir, config.thumbnail_max_edge, config.thumbnail_quality
        )
    except thumbnailer.ThumbnailError as exc:
        log.warning("thumbnail failed: %s", exc)
        repository.set_thumbnail(conn, image_id, None, "failed", utc_now())
        run.thumbnail_failed_count += 1
        return
    repository.set_thumbnail(conn, image_id, name, "ok", utc_now())
    run.thumbnail_generated_count += 1
