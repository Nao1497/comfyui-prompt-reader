"""Shared fixtures: temporary scan_root/db config and PNG generators."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, PngImagePlugin

from app import db
from app.config import AppConfig

_COLOR_SEQ = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)]


def make_png(
    path: Path,
    size: tuple[int, int] = (64, 48),
    color: tuple[int, ...] | None = None,
    mode: str = "RGB",
    prompt: dict[str, Any] | str | None = None,
    workflow: dict[str, Any] | str | None = None,
    mtime: float | None = None,
    seed: int = 0,
) -> Path:
    """Write a PNG at ``path``; ``prompt``/``workflow`` become tEXt chunks like ComfyUI."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if color is None:
        color = _COLOR_SEQ[seed % len(_COLOR_SEQ)]
        if mode == "RGBA":
            color = (*color, 128)
    img = Image.new(mode, size, color)
    # Make the content unique per (path, seed) so hashes differ.
    img.putpixel((0, 0), tuple((c + seed) % 256 for c in color))
    info = PngImagePlugin.PngInfo()
    if prompt is not None:
        info.add_text("prompt", prompt if isinstance(prompt, str) else json.dumps(prompt))
    if workflow is not None:
        info.add_text("workflow", workflow if isinstance(workflow, str) else json.dumps(workflow))
    img.save(path, format="PNG", pnginfo=info)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def make_broken_png(path: Path, kind: str = "garbage") -> Path:
    """Write a file with a .png name that Pillow cannot decode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "garbage":
        path.write_bytes(b"this is not a png at all" * 10)
    elif kind == "truncated":
        tmp = path.with_suffix(".tmp")
        make_png(tmp, size=(256, 256))
        data = tmp.read_bytes()
        tmp.unlink()
        path.write_bytes(data[: len(data) // 2])
    else:
        raise ValueError(kind)
    return path


@pytest.fixture
def scan_root(tmp_path: Path) -> Path:
    root = tmp_path / "images"
    root.mkdir()
    return root


@pytest.fixture
def config(tmp_path: Path, scan_root: Path) -> AppConfig:
    return AppConfig(scan_root=scan_root, db_path=tmp_path / "data" / "images.db")


@pytest.fixture
def conn(config: AppConfig):
    c = db.open_database(config.db_path)
    yield c
    c.close()


@pytest.fixture
def sqlite_row_factory() -> type[sqlite3.Row]:
    return sqlite3.Row
