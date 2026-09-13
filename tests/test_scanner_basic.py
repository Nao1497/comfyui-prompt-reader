import hashlib
import os
from pathlib import Path

import pytest

from app import repository, scanner
from app.config import AppConfig
from tests.conftest import make_png


def test_scan_root_missing_raises_and_changes_nothing(tmp_path: Path, conn):
    cfg = AppConfig(scan_root=tmp_path / "missing", db_path=tmp_path / "db.sqlite", rename_on_scan=False)
    with pytest.raises(scanner.ScanRootNotFound):
        scanner.run_scan(cfg, conn)
    assert repository.count_images(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 0


def test_recursive_registration_and_relative_paths(config, conn, scan_root: Path):
    make_png(scan_root / "root.png", seed=1)
    make_png(scan_root / "sub" / "a.PNG", seed=2)
    make_png(scan_root / "sub" / "deep" / "b.png", seed=3)
    (scan_root / "sub" / "notes.txt").write_text("ignored")

    run = scanner.run_scan(config, conn)

    assert run.scanned_count == 3
    assert run.created_count == 3
    images = {img.file_path: img for img in repository.list_all_images(conn)}
    assert set(images) == {"root.png", "sub/a.PNG", "sub/deep/b.png"}
    assert images["root.png"].dir_path == ""
    assert images["sub/a.PNG"].dir_path == "sub"
    assert images["sub/deep/b.png"].dir_path == "sub/deep"
    assert images["sub/deep/b.png"].file_name == "b.png"
    assert all(img.presence == "active" for img in images.values())
    assert (scan_root / config.thumbnail_dir_name).is_dir()


def test_second_scan_does_not_duplicate(config, conn, scan_root: Path):
    make_png(scan_root / "a.png", seed=1)
    make_png(scan_root / "b.png", seed=2)

    first = scanner.run_scan(config, conn)
    second = scanner.run_scan(config, conn)

    assert first.created_count == 2
    assert second.created_count == 0
    assert second.scanned_count == 2
    assert repository.count_images(conn) == 2
    assert conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 2


def test_thumbnail_dir_and_dot_dirs_are_excluded(config, conn, scan_root: Path):
    make_png(scan_root / "keep.png", seed=1)
    make_png(scan_root / ".thumbnails" / "x.png", seed=2)
    make_png(scan_root / ".hidden" / "y.png", seed=3)
    make_png(scan_root / "sub" / ".git" / "z.png", seed=4)

    run = scanner.run_scan(config, conn)

    assert run.scanned_count == 1
    assert [img.file_path for img in repository.list_all_images(conn)] == ["keep.png"]


def test_duplicate_content_in_one_scan_keeps_first_path(config, conn, scan_root: Path):
    # 確認事項 #6: first path in sorted walk order wins.
    src = make_png(scan_root / "b" / "same.png", seed=1)
    (scan_root / "a").mkdir()
    (scan_root / "a" / "same.png").write_bytes(src.read_bytes())

    run = scanner.run_scan(config, conn)

    assert run.scanned_count == 2
    assert run.created_count == 1
    assert [img.file_path for img in repository.list_all_images(conn)] == ["a/same.png"]


def test_file_info_is_recorded(config, conn, scan_root: Path):
    path = make_png(scan_root / "info.png", size=(120, 80), mtime=1_700_000_000)

    scanner.run_scan(config, conn)

    img = repository.list_all_images(conn)[0]
    assert img.file_name == "info.png"
    assert img.file_size == path.stat().st_size
    assert img.image_width == 120
    assert img.image_height == 80
    assert img.file_mtime == "2023-11-14T22:13:20Z"
    assert img.content_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert img.extraction_status == "none"
    assert img.thumbnail_status == "ok"  # thumbnails generated since TASK-15
    assert img.is_favorite is False


def test_broken_png_is_registered_without_stopping(config, conn, scan_root: Path):
    from tests.conftest import make_broken_png

    make_png(scan_root / "ok.png", seed=1)
    make_broken_png(scan_root / "bad.png")

    run = scanner.run_scan(config, conn)

    assert run.scanned_count == 2
    assert run.created_count == 2
    bad = {img.file_path: img for img in repository.list_all_images(conn)}["bad.png"]
    assert bad.image_width is None and bad.image_height is None
    assert bad.thumbnail_status == "failed"
    assert bad.extraction_status == "none"
