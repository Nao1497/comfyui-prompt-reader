import hashlib
import shutil
from pathlib import Path

from PIL import Image

from app import repository, scanner, thumbnailer
from tests.conftest import make_broken_png, make_png


def _png_snapshot(root: Path, thumb_dir: str) -> dict[str, str]:
    out = {}
    for p in root.rglob("*"):
        if p.is_file() and thumb_dir not in p.relative_to(root).parts:
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_thumbnails_are_generated_for_new_images(config, conn, scan_root):
    make_png(scan_root / "a.png", size=(2000, 1000), seed=1)
    make_png(scan_root / "sub" / "b.png", size=(100, 50), seed=2)

    run = scanner.run_scan(config, conn)

    assert run.thumbnail_generated_count == 2
    assert run.thumbnail_failed_count == 0
    for img in repository.list_all_images(conn):
        assert img.thumbnail_status == "ok"
        thumb = config.thumbnail_dir / img.thumbnail_name
        assert thumb.is_file()
        with Image.open(thumb) as t:
            assert t.format == "WEBP"
            assert max(t.size) <= config.thumbnail_max_edge
            assert t.size[0] * img.image_height == t.size[1] * img.image_width  # same aspect
    assert all(p.parent == config.thumbnail_dir for p in config.thumbnail_dir.iterdir())


def test_sources_untouched_and_thumbnails_not_registered(config, conn, scan_root):
    make_png(scan_root / "a.png", seed=1)
    make_png(scan_root / "sub" / "b.png", seed=2)
    before = _png_snapshot(scan_root, config.thumbnail_dir_name)

    scanner.run_scan(config, conn)
    after = _png_snapshot(scan_root, config.thumbnail_dir_name)
    assert after == before

    new_files = [p for p in scan_root.rglob("*") if p.is_file() and p.relative_to(scan_root).as_posix() not in before]
    assert new_files and all(p.parent == config.thumbnail_dir for p in new_files)

    second = scanner.run_scan(config, conn)
    assert second.scanned_count == 2
    assert repository.count_images(conn) == 2
    assert not any(config.thumbnail_dir_name in img.file_path for img in repository.list_all_images(conn))


def test_broken_png_is_recorded_as_failed_and_others_succeed(client, scan_root, config):
    from app import db

    make_png(scan_root / "ok1.png", seed=1)
    make_broken_png(scan_root / "garbage.png", kind="garbage")
    make_broken_png(scan_root / "truncated.png", kind="truncated")
    make_png(scan_root / "ok2.png", seed=2)

    resp = client.post("/scan")

    assert resp.status_code == 200
    run = resp.json()
    assert run["scannedCount"] == 4
    assert run["createdCount"] == 4
    assert run["thumbnailGeneratedCount"] == 2
    assert run["thumbnailFailedCount"] == 2
    conn = db.connect(config.db_path)
    status = {i.file_path: i.thumbnail_status for i in repository.list_all_images(conn)}
    conn.close()
    assert status == {"ok1.png": "ok", "ok2.png": "ok", "garbage.png": "failed", "truncated.png": "failed"}


def test_second_scan_generates_nothing(client, scan_root):
    make_png(scan_root / "a.png", seed=1)
    make_png(scan_root / "b.png", seed=2)
    first = client.post("/scan").json()
    second = client.post("/scan").json()
    assert first["thumbnailGeneratedCount"] == 2
    assert second["thumbnailGeneratedCount"] == 0
    assert second["thumbnailFailedCount"] == 0


def test_failed_thumbnail_is_retried_next_scan(config, conn, scan_root, monkeypatch):
    make_png(scan_root / "flaky.png", seed=1)
    real = thumbnailer.generate_thumbnail
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise thumbnailer.ThumbnailError("simulated")
        return real(*args, **kwargs)

    monkeypatch.setattr(thumbnailer, "generate_thumbnail", flaky)

    first = scanner.run_scan(config, conn)
    img = repository.list_all_images(conn)[0]
    assert first.thumbnail_failed_count == 1
    assert img.thumbnail_status == "failed" and img.thumbnail_name is None

    second = scanner.run_scan(config, conn)
    img = repository.list_all_images(conn)[0]
    assert second.thumbnail_generated_count == 1
    assert img.thumbnail_status == "ok" and img.thumbnail_name

    third = scanner.run_scan(config, conn)
    assert third.thumbnail_generated_count == 0
    assert calls["n"] == 2


def test_moved_file_keeps_thumbnail_name(config, conn, scan_root):
    src = make_png(scan_root / "x" / "m.png", seed=1)
    scanner.run_scan(config, conn)
    before = repository.list_all_images(conn)[0]
    (scan_root / "y").mkdir()
    shutil.move(src, scan_root / "y" / "m.png")

    run = scanner.run_scan(config, conn)

    after = repository.list_all_images(conn)[0]
    assert run.thumbnail_generated_count == 0
    assert after.thumbnail_name == before.thumbnail_name


def test_thumbnail_survives_missing_original(config, conn, scan_root):
    path = make_png(scan_root / "gone.png", seed=1)
    scanner.run_scan(config, conn)
    name = repository.list_all_images(conn)[0].thumbnail_name

    path.unlink()
    scanner.run_scan(config, conn)

    img = repository.list_all_images(conn)[0]
    assert img.presence == "missing"
    assert img.thumbnail_name == name
    assert (config.thumbnail_dir / name).is_file()
