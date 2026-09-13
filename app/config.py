"""Load ``config.toml`` into an :class:`AppConfig` (design.md §1 "設定")."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when config.toml is missing required settings or is invalid."""


@dataclass(frozen=True)
class AppConfig:
    scan_root: Path
    thumbnail_dir_name: str = ".thumbnails"
    thumbnail_max_edge: int = 768
    thumbnail_quality: int = 80
    grid_min_cell: int = 96
    grid_max_cell: int = 320
    db_path: Path = Path("./data/images.db")
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def thumbnail_dir(self) -> Path:
        return self.scan_root / self.thumbnail_dir_name


_DEFAULTS = {
    "thumbnail_dir_name": ".thumbnails",
    "thumbnail_max_edge": 768,
    "thumbnail_quality": 80,
    "grid_min_cell": 96,
    "grid_max_cell": 320,
    "db_path": "./data/images.db",
    "host": "127.0.0.1",
    "port": 8000,
}


def load_config(path: str | Path) -> AppConfig:
    """Read ``path`` (TOML) and return an :class:`AppConfig`.

    ``scan_root`` is required; every other key falls back to the design.md default.
    A relative ``db_path`` is resolved against the directory containing the
    config file (確認事項 #15).
    """
    path = Path(path)
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    if "scan_root" not in raw:
        raise ConfigError("config.toml must define scan_root")

    values = {**_DEFAULTS, **raw}
    base_dir = path.resolve().parent
    db_path = Path(values["db_path"])
    if not db_path.is_absolute():
        db_path = base_dir / db_path

    return AppConfig(
        scan_root=Path(values["scan_root"]),
        thumbnail_dir_name=str(values["thumbnail_dir_name"]),
        thumbnail_max_edge=int(values["thumbnail_max_edge"]),
        thumbnail_quality=int(values["thumbnail_quality"]),
        grid_min_cell=int(values["grid_min_cell"]),
        grid_max_cell=int(values["grid_max_cell"]),
        db_path=db_path,
        host=str(values["host"]),
        port=int(values["port"]),
    )
