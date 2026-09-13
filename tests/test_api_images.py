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


# --- favorite_only / include_missing / missing_only ---------------------------

def _ids(resp):
    return {i["id"] for i in resp.json()["items"]}


def test_favorite_only_filter(client, scan_root):
    _seed_images(client, scan_root, 3)
    ids = sorted(_ids(client.get("/images")))
    fav = ids[0]

    client.put(f"/images/{fav}/favorite", json={"is_favorite": True})
    resp = client.get("/images", params={"favorite_only": "true"})
    assert _ids(resp) == {fav}
    assert resp.json()["totalCount"] == 1

    client.put(f"/images/{fav}/favorite", json={"is_favorite": False})
    resp = client.get("/images", params={"favorite_only": "true"})
    assert _ids(resp) == set()
    assert resp.json()["totalCount"] == 0
    assert client.get("/images").json()["totalCount"] == 3


def test_missing_is_excluded_by_default(client, scan_root):
    _seed_images(client, scan_root, 2)
    (scan_root / "img00.png").unlink()
    client.post("/scan")
    all_items = {i["fileName"]: i for i in client.get("/images", params={"include_missing": "true"}).json()["items"]}
    gone = all_items["img00.png"]["id"]

    default = client.get("/images")
    assert gone not in _ids(default)
    assert default.json()["totalCount"] == 1

    with_missing = client.get("/images", params={"include_missing": "true"})
    assert gone in _ids(with_missing)
    assert with_missing.json()["totalCount"] == 2

    only_missing = client.get("/images", params={"missing_only": "true"})
    assert _ids(only_missing) == {gone}
    assert only_missing.json()["totalCount"] == 1

    # Detail still works for a missing image.
    assert client.get(f"/images/{gone}").json()["presence"] == "missing"


def test_filters_combine_with_cursor(client, scan_root):
    _seed_images(client, scan_root, 5, same_mtime=True)
    ids = sorted(_ids(client.get("/images")))
    for i in ids[:4]:
        client.put(f"/images/{i}/favorite", json={"is_favorite": True})

    first = client.get("/images", params={"favorite_only": "true", "limit": 3}).json()
    second = client.get(
        "/images", params={"favorite_only": "true", "limit": 3, "cursor": first["nextCursor"]}
    ).json()
    got = [i["id"] for i in first["items"] + second["items"]]
    assert sorted(got) == ids[:4]
    assert second["nextCursor"] is None


# --- dir / recursive (TASK-18) ------------------------------------------------

def _names(resp):
    return sorted(i["filePath"] for i in resp.json()["items"])


def _seed_tree(client, scan_root):
    for i, rel in enumerate(["root.png", "a/1.png", "a/b/2.png", "a/b/c/3.png", "ab/4.png"]):
        make_png(scan_root / rel, seed=i + 1)
    client.post("/scan")


def test_dir_non_recursive_returns_direct_children_only(client, scan_root):
    _seed_tree(client, scan_root)
    resp = client.get("/images", params={"dir": "a", "recursive": "false"})
    assert _names(resp) == ["a/1.png"]
    assert resp.json()["totalCount"] == 1


def test_dir_recursive_includes_subfolders_but_not_prefix_siblings(client, scan_root):
    _seed_tree(client, scan_root)
    resp = client.get("/images", params={"dir": "a", "recursive": "true"})
    assert _names(resp) == ["a/1.png", "a/b/2.png", "a/b/c/3.png"]
    assert resp.json()["totalCount"] == 3
    # default recursive=true
    assert _names(client.get("/images", params={"dir": "a/b"})) == ["a/b/2.png", "a/b/c/3.png"]


def test_empty_dir_semantics(client, scan_root):
    _seed_tree(client, scan_root)
    root_only = client.get("/images", params={"dir": "", "recursive": "false"})
    assert _names(root_only) == ["root.png"]
    everything = client.get("/images", params={"dir": "", "recursive": "true"})
    assert everything.json()["totalCount"] == 5
    assert client.get("/images").json()["totalCount"] == 5


def test_dir_combines_with_other_filters_and_cursor(client, scan_root):
    _seed_tree(client, scan_root)
    ids = {i["filePath"]: i["id"] for i in client.get("/images").json()["items"]}
    client.put(f"/images/{ids['a/b/2.png']}/favorite", json={"is_favorite": True})
    client.put(f"/images/{ids['ab/4.png']}/favorite", json={"is_favorite": True})

    resp = client.get("/images", params={"dir": "a", "favorite_only": "true"})
    assert _names(resp) == ["a/b/2.png"]

    first = client.get("/images", params={"dir": "a", "limit": 2}).json()
    second = client.get("/images", params={"dir": "a", "limit": 2, "cursor": first["nextCursor"]}).json()
    got = sorted(i["filePath"] for i in first["items"] + second["items"])
    assert got == ["a/1.png", "a/b/2.png", "a/b/c/3.png"]


def test_unknown_dir_is_empty_not_error(client, scan_root):
    _seed_tree(client, scan_root)
    resp = client.get("/images", params={"dir": "nope"})
    assert resp.status_code == 200
    assert resp.json()["items"] == [] and resp.json()["totalCount"] == 0
