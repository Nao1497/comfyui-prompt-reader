from pathlib import Path

import pytest

from app.config import AppConfig, ConfigError, load_config


def test_defaults_are_filled_in(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('scan_root = "/images"\n')

    cfg = load_config(cfg_file)

    assert isinstance(cfg, AppConfig)
    assert cfg.scan_root == Path("/images")
    assert cfg.thumbnail_dir_name == ".thumbnails"
    assert cfg.thumbnail_max_edge == 768
    assert cfg.thumbnail_quality == 80
    assert cfg.grid_min_cell == 96
    assert cfg.grid_max_cell == 320
    assert cfg.db_path == tmp_path / "data" / "images.db"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8000
    assert cfg.rename_on_scan is True
    assert cfg.thumbnail_dir == Path("/images/.thumbnails")


def test_all_values_can_be_overridden(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "\n".join(
            [
                'scan_root = "/data/out"',
                'thumbnail_dir_name = ".thumbs"',
                "thumbnail_max_edge = 512",
                "thumbnail_quality = 60",
                "grid_min_cell = 64",
                "grid_max_cell = 400",
                'db_path = "/var/lib/reader/images.db"',
                'host = "0.0.0.0"',
                "port = 9000",
                "rename_on_scan = false",
            ]
        )
    )

    cfg = load_config(cfg_file)

    assert cfg.scan_root == Path("/data/out")
    assert cfg.thumbnail_dir_name == ".thumbs"
    assert cfg.thumbnail_max_edge == 512
    assert cfg.thumbnail_quality == 60
    assert cfg.grid_min_cell == 64
    assert cfg.grid_max_cell == 400
    assert cfg.db_path == Path("/var/lib/reader/images.db")
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9000
    assert cfg.rename_on_scan is False


def test_missing_scan_root_raises(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("port = 8000\n")

    with pytest.raises(ConfigError):
        load_config(cfg_file)


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")
