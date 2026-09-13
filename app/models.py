"""Dataclasses mirroring the SQLite tables (design.md §2)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Image:
    id: int
    content_hash: str
    file_path: str
    dir_path: str
    file_name: str
    file_size: int
    image_width: int | None
    image_height: int | None
    file_mtime: str
    presence: str
    is_favorite: bool
    thumbnail_name: str | None
    thumbnail_status: str
    extraction_status: str
    positive_prompt: str | None
    negative_prompt: str | None
    model_name: str | None
    seed: int | None
    steps: int | None
    cfg: float | None
    sampler_name: str | None
    scheduler: str | None
    gen_width: int | None
    gen_height: int | None
    created_at: str
    updated_at: str


@dataclass
class RawMetadata:
    image_id: int
    prompt_json: str | None
    workflow_json: str | None


@dataclass
class ScanRun:
    id: int
    started_at: str
    finished_at: str | None
    scanned_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    missing_count: int = 0
    extract_failed_count: int = 0
    thumbnail_generated_count: int = 0
    thumbnail_failed_count: int = 0
    renamed_count: int = 0
    error: str | None = None


@dataclass
class Lora:
    id: int
    name: str
    file_name: str
    file_size: int | None
    file_mtime: str | None
    presence: str
    trigger_words: str
    memo: str
    created_at: str
    updated_at: str
    image_count: int = 0


@dataclass
class LoraUsage:
    """One LoRA referenced by an image's workflow."""
    name: str
    strength_model: float | None = None
    strength_clip: float | None = None


@dataclass
class Tag:
    id: int
    name: str
    name_normalized: str
    category: int | None
    category_name: str
    post_count: int
    tag_created_at: str | None
    aliases: str
    other_names: str
    posts_url: str | None
    wiki_url: str | None
    has_wiki: str | None
    source: str
    lora_id: int | None
    created_at: str
    updated_at: str
    image_count: int = 0


@dataclass
class TagImport:
    id: int
    started_at: str
    finished_at: str | None
    file_name: str | None
    read_count: int = 0
    imported_count: int = 0
    skipped_count: int = 0
    alias_count: int = 0
    linked_image_count: int = 0
    error: str | None = None
