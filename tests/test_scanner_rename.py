import hashlib
import re
from datetime import datetime
from pathlib import Path

import pytest

from app import repository, scanner
from app.config import AppConfig
from tests.conftest import make_png

NAME_RE = re.compile(r"^\d{8}T\d{6}_[0-9a-f]{8}\.png$")
MTIME = 1_772_000_000  # 2026-02-27 (UTC) - local stamp derived below


@pytest.fixture
def rename_config(tmp_path: Path, scan_root: Path) -> AppConfig:
    return AppConfig(scan_root=scan_root, db_path=tmp_path / "data" / "images.db", rename_on_scan=True)


def test_rename_helpers():
    assert scanner.is_canonical_name("20260227T143052_a3f1c9d2.png")
    assert not scanner.is_canonical_name("20260227T143052_a3f1c9d2.PNG")
    assert not scanner.is_canonical_name("ComfyUI_00042_.png")
    assert not scanner.is_canonical_name("20260227T143052_A3F1C9D2.png")


def test_files_are_renamed_in_place_with_mtime_stamp(rename_config, conn, scan_root):
    src = make_png(scan_root / "sub" / "ComfyUI_00042_.png", seed=1, mtime=MTIME)
    upper = make_png(scan_root / "Photo.PNG", seed=2, mtime=MTIME + 60)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (src, upper)}

    run = scanner.run_scan(rename_config, conn)

    assert run.renamed_count == 2
    assert not src.exists() and not upper.exists()
    files = sorted(p for p in scan_root.rglob("*.png") if ".thumbnails" not in p.parts)
    assert len(files) == 2
    for p in files:
        assert NAME_RE.match(p.name), p.name
    renamed_sub = next(p for p in files if p.parent == scan_root / "sub")
    expected_stamp = datetime.fromtimestamp(MTIME).strftime("%Y%m%dT%H%M%S")
    assert renamed_sub.name.startswith(expected_stamp + "_")
    assert int(renamed_sub.stat().st_mtime) == MTIME  # mtime preserved
    assert hashlib.sha256(renamed_sub.read_bytes()).hexdigest() == before[src]

    images = {i.file_path: i for i in repository.list_all_images(conn)}
    assert set(images) == {f"sub/{renamed_sub.name}", next(p.name for p in files if p.parent == scan_root)}
    assert images[f"sub/{renamed_sub.name}"].file_name == renamed_sub.name
    assert images[f"sub/{renamed_sub.name}"].dir_path == "sub"


def test_second_scan_does_not_rename_again(rename_config, conn, scan_root):
    make_png(scan_root / "a.png", seed=1, mtime=MTIME)
    scanner.run_scan(rename_config, conn)
    names_after_first = sorted(p.name for p in scan_root.glob("*.png"))

    run = scanner.run_scan(rename_config, conn)

    assert run.renamed_count == 0
    assert run.updated_count == 0
    assert sorted(p.name for p in scan_root.glob("*.png")) == names_after_first


def test_existing_record_follows_the_rename_and_keeps_favorite(tmp_path, conn, scan_root):
    off = AppConfig(scan_root=scan_root, db_path=tmp_path / "db", rename_on_scan=False)
    on = AppConfig(scan_root=scan_root, db_path=tmp_path / "db", rename_on_scan=True)
    make_png(scan_root / "old_name.png", seed=1, mtime=MTIME)
    scanner.run_scan(off, conn)
    before = repository.list_all_images(conn)[0]
    repository.set_favorite(conn, before.id, True, "2026-01-01T00:00:00Z")
    conn.commit()

    run = scanner.run_scan(on, conn)

    after = repository.list_all_images(conn)[0]
    assert run.renamed_count == 1 and run.created_count == 0 and run.missing_count == 0
    assert after.id == before.id
    assert NAME_RE.match(after.file_name)
    assert after.is_favorite is True
    assert after.thumbnail_name == before.thumbnail_name
    assert after.presence == "active"


def test_rename_disabled_keeps_names(config, conn, scan_root):
    make_png(scan_root / "keep_me.png", seed=1)
    run = scanner.run_scan(config, conn)
    assert run.renamed_count == 0
    assert (scan_root / "keep_me.png").exists()


def test_rename_failure_does_not_stop_scan(rename_config, conn, scan_root, monkeypatch):
    make_png(scan_root / "a.png", seed=1)
    make_png(scan_root / "b.png", seed=2)

    def boom(path):
        if path.name == "a.png":
            raise OSError("simulated permission error")
        return scanner.rename_to_canonical.__wrapped__(path)

    real = scanner.rename_to_canonical
    boom.__wrapped__ = real
    monkeypatch.setattr(scanner, "rename_to_canonical", boom)

    run = scanner.run_scan(rename_config, conn)

    assert run.created_count == 2
    assert run.renamed_count == 1
    assert (scan_root / "a.png").exists()


def test_scan_endpoint_reports_renamed_count(tmp_path, scan_root):
    from fastapi.testclient import TestClient

    from app.api import create_app

    cfg = AppConfig(scan_root=scan_root, db_path=tmp_path / "db", rename_on_scan=True)
    make_png(scan_root / "x.png", seed=1)
    with TestClient(create_app(cfg)) as c:
        body = c.post("/scan").json()
        assert body["renamedCount"] == 1
        item = c.get("/images").json()["items"][0]
        assert NAME_RE.match(item["fileName"])
        assert c.get(f"/images/{item['id']}/file").status_code == 200
