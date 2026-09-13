import threading
import time

from app import scanner
from app.config import AppConfig
from tests.conftest import make_png

SCAN_KEYS = {
    "scanRunId", "startedAt", "finishedAt", "scannedCount", "createdCount",
    "updatedCount", "missingCount", "extractFailedCount",
    "thumbnailGeneratedCount", "thumbnailFailedCount", "renamedCount",
}


def test_scan_returns_counts(client, scan_root):
    make_png(scan_root / "a.png", seed=1)
    make_png(scan_root / "sub" / "b.png", seed=2)

    resp = client.post("/scan")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == SCAN_KEYS
    assert body["scannedCount"] == 2
    assert body["createdCount"] == 2
    assert body["scanRunId"] >= 1
    assert body["startedAt"].endswith("Z") and body["finishedAt"].endswith("Z")


def test_invalid_scan_root_returns_400_and_changes_nothing(tmp_path, scan_root, config):
    from fastapi.testclient import TestClient

    from app import db, repository
    from app.api import create_app

    make_png(scan_root / "a.png", seed=1)
    with TestClient(create_app(config)) as c:
        assert c.post("/scan").status_code == 200

    bad = AppConfig(scan_root=tmp_path / "does-not-exist", db_path=config.db_path, rename_on_scan=False)
    with TestClient(create_app(bad)) as c:
        resp = c.post("/scan")

    assert resp.status_code == 400
    assert resp.json() == {"error": {"code": "INVALID_SCAN_ROOT", "message": "scan root does not exist"}}
    conn = db.connect(config.db_path)
    assert repository.count_images(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 1
    conn.close()


def test_concurrent_scan_is_rejected(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    real_run_scan = scanner.run_scan

    def slow_run_scan(config, conn):
        started.set()
        release.wait(timeout=5)
        return real_run_scan(config, conn)

    monkeypatch.setattr(scanner, "run_scan", slow_run_scan)

    results = {}

    def first():
        results["first"] = client.post("/scan").status_code

    t = threading.Thread(target=first)
    t.start()
    assert started.wait(timeout=5)
    second = client.post("/scan")
    release.set()
    t.join(timeout=10)

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "SCAN_IN_PROGRESS"
    assert results["first"] == 200
    assert client.post("/scan").status_code == 200
