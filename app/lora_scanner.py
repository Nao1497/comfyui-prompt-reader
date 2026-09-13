"""LoRA folder scan and workflow back-fill (FR-41)."""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app import repository, scanner
from app.config import AppConfig

log = logging.getLogger(__name__)

LORA_EXTENSIONS = {".safetensors", ".pt", ".ckpt"}


class LoraRootNotFound(Exception):
    """lora_root is configured but does not exist."""


@dataclass
class LoraScanResult:
    lora_root_configured: bool
    scanned_file_count: int = 0
    file_created_count: int = 0
    file_missing_count: int = 0
    backfilled_image_count: int = 0
    linked_lora_count: int = 0


def iter_lora_files(lora_root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(lora_root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in filenames:
            if Path(name).suffix.lower() in LORA_EXTENSIONS and not name.startswith("."):
                found.append(Path(dirpath) / name)
    found.sort()
    return found


def run_lora_scan(config: AppConfig, conn: sqlite3.Connection) -> LoraScanResult:
    """Register LoRA files under lora_root, then link every stored workflow to its LoRAs."""
    result = LoraScanResult(lora_root_configured=config.lora_root is not None)
    now = scanner.utc_now()

    if config.lora_root is not None:
        if not config.lora_root.is_dir():
            raise LoraRootNotFound(f"lora root does not exist: {config.lora_root}")
        seen: set[int] = set()
        for path in iter_lora_files(config.lora_root):
            result.scanned_file_count += 1
            name = path.relative_to(config.lora_root).as_posix()
            existed = repository.find_lora_by_name(conn, name) is not None
            lora_id = repository.upsert_lora_by_name(conn, name, now)
            stat = path.stat()
            repository.set_lora_file(conn, lora_id, stat.st_size, scanner.mtime_to_iso(stat.st_mtime), now)
            if not existed:
                result.file_created_count += 1
            seen.add(lora_id)
        result.file_missing_count = repository.mark_loras_missing_except(conn, seen, now)
        conn.commit()

    # Back-fill image_loras from every stored prompt (images registered before this
    # feature, or after edits to the extraction rules).
    for row in repository.iter_raw_prompts(conn):
        prompt = scanner.parse_prompt(row["prompt_json"])
        if prompt is None:
            continue
        linked = scanner.record_lora_usage(conn, int(row["image_id"]), prompt, now)
        if linked:
            result.backfilled_image_count += 1
            result.linked_lora_count += linked
    conn.commit()
    return result
