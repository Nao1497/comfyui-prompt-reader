import io

from PIL import Image

from tests.conftest import make_broken_png, make_png


def _id_of(client, file_name, include_missing=False):
    params = {"include_missing": "true"} if include_missing else {}
    items = client.get("/images", params=params).json()["items"]
    return next(i["id"] for i in items if i["fileName"] == file_name)


def test_thumbnail_is_served_as_webp_with_cache_header(client, scan_root):
    make_png(scan_root / "a.png", size=(1600, 800))
    client.post("/scan")
    image_id = _id_of(client, "a.png")

    resp = client.get(f"/images/{image_id}/thumbnail")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert resp.headers["cache-control"] == "public, max-age=86400"
    with Image.open(io.BytesIO(resp.content)) as img:
        assert img.format == "WEBP"
        assert img.size == (768, 384)


def test_thumbnail_available_for_missing_original(client, scan_root):
    path = make_png(scan_root / "gone.png")
    client.post("/scan")
    path.unlink()
    client.post("/scan")

    default_items = client.get("/images").json()["items"]
    assert all(i["fileName"] != "gone.png" for i in default_items)
    image_id = _id_of(client, "gone.png", include_missing=True)
    item = next(i for i in client.get("/images", params={"include_missing": "true"}).json()["items"] if i["id"] == image_id)
    assert item["presence"] == "missing"

    resp = client.get(item["thumbnailUrl"])
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"

    file_resp = client.get(f"/images/{image_id}/file")
    assert file_resp.status_code == 404
    assert file_resp.json()["error"]["code"] == "FILE_MISSING"


def test_file_is_served_as_png_bytes(client, scan_root):
    path = make_png(scan_root / "orig.png", size=(50, 40))
    client.post("/scan")
    image_id = _id_of(client, "orig.png")

    resp = client.get(f"/images/{image_id}/file")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == path.read_bytes()


def test_failed_thumbnail_is_unavailable(client, scan_root):
    make_broken_png(scan_root / "bad.png")
    client.post("/scan")
    image_id = _id_of(client, "bad.png")
    resp = client.get(f"/images/{image_id}/thumbnail")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "THUMBNAIL_UNAVAILABLE"


def test_thumbnail_file_deleted_on_disk_is_unavailable(client, scan_root, config):
    make_png(scan_root / "a.png")
    client.post("/scan")
    image_id = _id_of(client, "a.png")
    for p in config.thumbnail_dir.iterdir():
        p.unlink()
    resp = client.get(f"/images/{image_id}/thumbnail")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "THUMBNAIL_UNAVAILABLE"


def test_unknown_id_for_binary_routes(client):
    for suffix in ("thumbnail", "file"):
        resp = client.get(f"/images/8888/{suffix}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"
