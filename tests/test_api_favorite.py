import hashlib

from tests.conftest import make_png


def _scan_one(client, scan_root):
    path = make_png(scan_root / "fav.png", seed=1)
    client.post("/scan")
    image_id = client.get("/images").json()["items"][0]["id"]
    return path, image_id


def test_toggle_favorite(client, scan_root):
    _, image_id = _scan_one(client, scan_root)

    on = client.put(f"/images/{image_id}/favorite", json={"is_favorite": True})
    assert on.status_code == 200
    assert on.json() == {"id": image_id, "isFavorite": True}
    assert client.get(f"/images/{image_id}").json()["isFavorite"] is True

    off = client.put(f"/images/{image_id}/favorite", json={"is_favorite": False})
    assert off.json() == {"id": image_id, "isFavorite": False}
    assert client.get(f"/images/{image_id}").json()["isFavorite"] is False


def test_favorite_is_idempotent(client, scan_root):
    _, image_id = _scan_one(client, scan_root)
    for _ in range(3):
        assert client.put(f"/images/{image_id}/favorite", json={"is_favorite": True}).status_code == 200
    assert client.get(f"/images/{image_id}").json()["isFavorite"] is True


def test_favorite_does_not_touch_the_file(client, scan_root):
    path, image_id = _scan_one(client, scan_root)
    before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    before_mtime = path.stat().st_mtime_ns

    client.put(f"/images/{image_id}/favorite", json={"is_favorite": True})
    client.put(f"/images/{image_id}/favorite", json={"is_favorite": False})

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_hash
    assert path.stat().st_mtime_ns == before_mtime
    assert client.get(f"/images/{image_id}").json()["contentHash"] == before_hash


def test_favorite_errors(client, scan_root):
    _, image_id = _scan_one(client, scan_root)
    missing = client.put("/images/999999/favorite", json={"is_favorite": True})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"

    bad = client.put(f"/images/{image_id}/favorite", json={"is_favorite": "yes"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.put(f"/images/{image_id}/favorite", json={}).status_code == 422
