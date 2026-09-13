from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.config import AppConfig
from tests.conftest import make_png
from tests.prompt_graphs import standard, with_loras


def _client(tmp_path: Path, scan_root: Path, lora_root: Path | None):
    cfg = AppConfig(scan_root=scan_root, db_path=tmp_path / "db", rename_on_scan=False, lora_root=lora_root)
    return TestClient(create_app(cfg), raise_server_exceptions=False)


def _seed_loras(lora_root: Path):
    lora_root.mkdir(parents=True, exist_ok=True)
    (lora_root / "detail.safetensors").write_bytes(b"x" * 100)
    (lora_root / "styles").mkdir(exist_ok=True)
    (lora_root / "styles" / "anime.safetensors").write_bytes(b"y" * 200)
    (lora_root / "old.pt").write_bytes(b"z")
    (lora_root / "readme.txt").write_text("not a lora")
    (lora_root / ".hidden").mkdir(exist_ok=True)
    (lora_root / ".hidden" / "h.safetensors").write_bytes(b"h")


def test_lora_scan_registers_files_and_links_workflows(tmp_path, scan_root):
    lora_root = tmp_path / "loras"
    _seed_loras(lora_root)
    make_png(scan_root / "a.png", prompt=with_loras(standard(), ("detail.safetensors", 0.8, 0.7)), seed=1)
    make_png(scan_root / "b.png", prompt=with_loras(standard(), ("styles\\anime.safetensors", 1.0, 1.0), ("unlisted.safetensors", 0.5, 0.5)), seed=2)

    with _client(tmp_path, scan_root, lora_root) as c:
        c.post("/scan")
        resp = c.post("/loras/scan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["loraRootConfigured"] is True
        assert body["scannedFileCount"] == 3
        assert body["fileCreatedCount"] == 1  # old.pt; the other two already exist from the workflows
        assert body["fileMissingCount"] == 0
        assert body["backfilledImageCount"] == 2
        assert body["linkedLoraCount"] == 3

        items = c.get("/loras").json()["items"]
        by_name = {l["name"]: l for l in items}
        assert list(by_name) == sorted(by_name)
        assert set(by_name) == {"detail.safetensors", "styles/anime.safetensors", "old.pt", "unlisted.safetensors"}
        assert by_name["detail.safetensors"]["presence"] == "active"
        assert by_name["detail.safetensors"]["fileSize"] == 100
        assert by_name["detail.safetensors"]["fileMtime"].endswith("Z")
        assert by_name["detail.safetensors"]["imageCount"] == 1
        assert by_name["styles/anime.safetensors"]["fileName"] == "anime.safetensors"
        assert by_name["old.pt"]["imageCount"] == 0
        assert by_name["unlisted.safetensors"]["presence"] == "unknown"

        # A removed file becomes missing; an unknown one stays unknown.
        (lora_root / "old.pt").unlink()
        second = c.post("/loras/scan").json()
        assert second["fileMissingCount"] == 1
        assert second["fileCreatedCount"] == 0
        by_name = {l["name"]: l for l in c.get("/loras").json()["items"]}
        assert by_name["old.pt"]["presence"] == "missing"
        assert by_name["unlisted.safetensors"]["presence"] == "unknown"


def test_lora_scan_without_root_only_backfills(tmp_path, scan_root):
    make_png(scan_root / "a.png", prompt=with_loras(standard(), ("d.safetensors", 1.0, 1.0)))
    with _client(tmp_path, scan_root, None) as c:
        c.post("/scan")
        # simulate an image registered before the feature: drop its links
        from app import db
        conn = db.connect(c.app.state.config.db_path)
        conn.execute("DELETE FROM image_loras")
        conn.commit()
        conn.close()
        body = c.post("/loras/scan").json()
        assert body["loraRootConfigured"] is False
        assert body["scannedFileCount"] == 0
        assert body["backfilledImageCount"] == 1
        assert c.get("/config").json()["loraRootConfigured"] is False
        lora = c.get("/loras").json()["items"][0]
        assert lora["imageCount"] == 1


def test_invalid_lora_root(tmp_path, scan_root):
    with _client(tmp_path, scan_root, tmp_path / "nope") as c:
        resp = c.post("/loras/scan")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_LORA_ROOT"


def test_get_and_update_lora_notes(tmp_path, scan_root):
    lora_root = tmp_path / "loras"
    _seed_loras(lora_root)
    with _client(tmp_path, scan_root, lora_root) as c:
        c.post("/loras/scan")
        lora = next(l for l in c.get("/loras").json()["items"] if l["name"] == "detail.safetensors")

        assert c.get(f"/loras/{lora['id']}").json()["triggerWords"] == ""
        resp = c.put(f"/loras/{lora['id']}", json={"trigger_words": "detailed, sharp focus", "memo": "weight 0.6-0.8\n line2"})
        assert resp.status_code == 200
        assert resp.json()["triggerWords"] == "detailed, sharp focus"
        assert resp.json()["memo"] == "weight 0.6-0.8\n line2"

        # partial update keeps the other field
        resp = c.put(f"/loras/{lora['id']}", json={"memo": "changed"})
        assert resp.json()["triggerWords"] == "detailed, sharp focus"
        assert resp.json()["memo"] == "changed"
        assert c.get(f"/loras/{lora['id']}").json()["memo"] == "changed"

        # notes survive a rescan
        c.post("/loras/scan")
        assert c.get(f"/loras/{lora['id']}").json()["triggerWords"] == "detailed, sharp focus"

        assert c.put("/loras/9999", json={"memo": "x"}).status_code == 404
        assert c.get("/loras/9999").status_code == 404
        bad = c.put(f"/loras/{lora['id']}", json={"memo": 5})
        assert bad.status_code == 422


def test_image_detail_shows_trigger_words(tmp_path, scan_root):
    make_png(scan_root / "a.png", prompt=with_loras(standard(), ("d.safetensors", 1.0, 1.0)))
    with _client(tmp_path, scan_root, None) as c:
        c.post("/scan")
        lora = c.get("/loras").json()["items"][0]
        c.put(f"/loras/{lora['id']}", json={"trigger_words": "magic words"})
        image_id = c.get("/images").json()["items"][0]["id"]
        assert c.get(f"/images/{image_id}").json()["loras"][0]["triggerWords"] == "magic words"
