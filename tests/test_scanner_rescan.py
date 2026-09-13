import shutil

from app import repository, scanner
from tests.conftest import make_png


def _single(conn):
    images = repository.list_all_images(conn)
    assert len(images) == 1
    return images[0]


def test_moved_file_updates_path_and_keeps_favorite(config, conn, scan_root):
    src = make_png(scan_root / "old" / "pic.png", seed=1)
    scanner.run_scan(config, conn)
    before = _single(conn)
    repository.set_favorite(conn, before.id, True, "2026-01-01T00:00:00Z")
    conn.commit()

    dst = scan_root / "new" / "renamed.png"
    dst.parent.mkdir()
    shutil.move(src, dst)
    run = scanner.run_scan(config, conn)

    after = _single(conn)
    assert run.created_count == 0
    assert run.updated_count == 1
    assert run.missing_count == 0
    assert after.id == before.id
    assert after.file_path == "new/renamed.png"
    assert after.dir_path == "new"
    assert after.file_name == "renamed.png"
    assert after.presence == "active"
    assert after.is_favorite is True
    assert after.thumbnail_name == before.thumbnail_name


def test_deleted_file_becomes_missing_and_keeps_favorite(config, conn, scan_root):
    path = make_png(scan_root / "gone.png", seed=1)
    make_png(scan_root / "stays.png", seed=2)
    scanner.run_scan(config, conn)
    gone = {i.file_path: i for i in repository.list_all_images(conn)}["gone.png"]
    repository.set_favorite(conn, gone.id, True, "2026-01-01T00:00:00Z")
    conn.commit()

    path.unlink()
    run = scanner.run_scan(config, conn)

    images = {i.file_path: i for i in repository.list_all_images(conn)}
    assert run.missing_count == 1
    assert run.updated_count == 0
    assert len(images) == 2
    assert images["gone.png"].presence == "missing"
    assert images["gone.png"].is_favorite is True
    assert images["stays.png"].presence == "active"

    # A further scan does not count it as newly missing again.
    assert scanner.run_scan(config, conn).missing_count == 0


def test_reappearing_file_becomes_active_again(config, conn, scan_root):
    path = make_png(scan_root / "back.png", seed=1)
    data = path.read_bytes()
    scanner.run_scan(config, conn)
    path.unlink()
    scanner.run_scan(config, conn)
    assert _single(conn).presence == "missing"

    path.write_bytes(data)
    run = scanner.run_scan(config, conn)

    assert _single(conn).presence == "active"
    assert run.created_count == 0
    assert run.updated_count == 0  # 確認事項 #4: reappearance is not an "update"


def test_scan_response_counts(client, scan_root):
    a = make_png(scan_root / "a.png", seed=1)
    make_png(scan_root / "b.png", seed=2)
    make_png(scan_root / "c.png", seed=3)
    first = client.post("/scan").json()
    assert (first["scannedCount"], first["createdCount"]) == (3, 3)

    (scan_root / "moved").mkdir()
    shutil.move(a, scan_root / "moved" / "a.png")
    (scan_root / "b.png").unlink()
    make_png(scan_root / "d.png", seed=4)

    second = client.post("/scan").json()

    assert second["scannedCount"] == 3
    assert second["createdCount"] == 1
    assert second["updatedCount"] == 1
    assert second["missingCount"] == 1
    assert second["extractFailedCount"] == 0
