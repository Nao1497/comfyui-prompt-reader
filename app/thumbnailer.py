"""WebP thumbnail generation (design.md §5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image


class ThumbnailError(Exception):
    """Raised when a thumbnail cannot be produced; the caller records status=failed."""


def new_thumbnail_name(now: datetime | None = None) -> str:
    """``YYYYMMDD_HHMMSS_<uuid4[:8]>.webp`` using local time of generation."""
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{uuid.uuid4().hex[:8]}.webp"


def generate_thumbnail(src_path: Path, out_dir: Path, max_edge: int, quality: int) -> str:
    """Write a WebP thumbnail of ``src_path`` into ``out_dir`` and return its file name.

    Keeps aspect ratio, never upscales (``Image.thumbnail`` only shrinks), keeps
    alpha, and never touches the source file. Any failure raises ThumbnailError.
    """
    name = new_thumbnail_name()
    out_path = out_dir / name
    try:
        with Image.open(src_path) as img:
            img.load()
            mode = "RGBA" if _has_alpha(img) else "RGB"
            converted = img.convert(mode)
            converted.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            out_dir.mkdir(parents=True, exist_ok=True)
            converted.save(out_path, format="WEBP", quality=quality, method=4)
    except Exception as exc:  # noqa: BLE001 - broken/unsupported input
        out_path.unlink(missing_ok=True)
        raise ThumbnailError(f"{src_path}: {exc}") from exc
    return name


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
