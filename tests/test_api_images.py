from tests.conftest import make_png

BASE = 1_700_000_000


def _seed_images(client, scan_root, n, same_mtime=False):
    for i in range(n):
        mtime = BASE if same_mtime else BASE + i
        make_png(scan_root / f"img{i:02d}.png", seed=i, mtime=mtime)
    assert client.post("/scan").status_code == 200


def test_list_is_sorted_by_mtime_desc_then_id_desc(client, scan_root):
    make_png(scan_root / "old.png", seed=1, mtime=BASE)
    make_png(scan_root / "new.png", seed=2, mtime=BASE + 100)
    make_png(scan_root / "mid_a.png", seed=3, mtime=BASE + 50)
    make_png(scan_root / "mid_b.png", seed=4, mtime=BASE + 50)
    client.post("/scan")

    items = client.get("/images").json()["items"]

    assert [i["fileName"] for i in items][:1] == ["new.png"]
    assert [i["fileName"] for i in items][-1] == "old.png"
    mids = [i for i in items if i["fileName"].startswith("mid")]
    assert mids[0]["id"] > mids[1]["id"]
    mtimes = [i["fileMtime"] for i in items]
    assert mtimes == sorted(mtimes, reverse=True)


def test_list_item_shape(client, scan_root):
    _seed_images(client, scan_root, 1)
    item = client.get("/images").json()["items"][0]
    assert set(item) == {
        "id", "fileName", "filePath", "dirPath", "fileSize", "imageWidth", "imageHeight",
        "fileMtime", "presence", "isFavorite", "thumbnailUrl", "thumbnailStatus",
        "extractionStatus",
    }
    assert item["thumbnailUrl"] == f"/images/{item['id']}/thumbnail"
    assert item["isFavorite"] is False


def test_cursor_pagination_has_no_duplicates_and_matches_total(client, scan_root):
    _seed_images(client, scan_root, 7, same_mtime=True)

    first = client.get("/images", params={"limit": 3}).json()
    assert first["totalCount"] == 7
    seen = [i["id"] for i in first["items"]]
    cursor = first["nextCursor"]
    pages = 1
    while cursor is not None:
        page = client.get("/images", params={"limit": 3, "cursor": cursor}).json()
        assert page["totalCount"] is None
        seen.extend(i["id"] for i in page["items"])
        cursor = page["nextCursor"]
        pages += 1

    assert len(seen) == len(set(seen)) == 7
    assert pages == 3


def test_next_cursor_null_when_page_not_full(client, scan_root):
    _seed_images(client, scan_root, 2)
    body = client.get("/images", params={"limit": 5}).json()
    assert body["nextCursor"] is None
    assert len(body["items"]) == 2


def test_limit_validation(client):
    assert client.get("/images", params={"limit": 0}).status_code == 422
    assert client.get("/images", params={"limit": 301}).status_code == 422
    assert client.get("/images", params={"limit": 300}).status_code == 200


def test_invalid_cursor_is_validation_error(client):
    resp = client.get("/images", params={"cursor": "!!!"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_detail_shape_and_null_generation(client, scan_root):
    _seed_images(client, scan_root, 1)
    image_id = client.get("/images").json()["items"][0]["id"]

    body = client.get(f"/images/{image_id}").json()

    assert body["id"] == image_id
    assert body["fileName"] == "img00.png"
    assert body["fileUrl"] == f"/images/{image_id}/file"
    assert body["thumbnailUrl"] == f"/images/{image_id}/thumbnail"
    assert len(body["contentHash"]) == 64
    assert body["extractionStatus"] == "none"
    assert set(body["generation"]) == {
        "positivePrompt", "negativePrompt", "modelName", "seed", "steps", "cfg",
        "samplerName", "scheduler", "genWidth", "genHeight",
    }
    assert all(v is None for v in body["generation"].values())


def test_unknown_id_is_json_404(client):
    resp = client.get("/images/999999")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"error": {"code": "NOT_FOUND", "message": "image not found"}}
