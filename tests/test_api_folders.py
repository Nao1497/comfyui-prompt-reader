from app.folders import build_tree
from tests.conftest import make_png


def _seed(scan_root, paths):
    for i, rel in enumerate(paths):
        make_png(scan_root / rel, seed=i + 1)


def test_tree_with_direct_and_total_counts(client, scan_root):
    _seed(scan_root, ["root.png", "a/1.png", "a/2.png", "a/b/3.png", "a/b/4.png", "a/b/5.png", "c/6.png"])
    client.post("/scan")

    body = client.get("/folders").json()

    assert body["rootTotalCount"] == 7
    assert [f["name"] for f in body["folders"]] == ["a", "c"]
    a, c = body["folders"]
    assert (a["path"], a["directCount"], a["totalCount"]) == ("a", 2, 5)
    assert len(a["children"]) == 1
    b = a["children"][0]
    assert (b["name"], b["path"], b["directCount"], b["totalCount"], b["children"]) == ("b", "a/b", 3, 3, [])
    assert (c["directCount"], c["totalCount"]) == (1, 1)
    assert set(body) == {"rootTotalCount", "favoriteCount", "missingCount", "folders"}


def test_intermediate_folder_without_images(client, scan_root):
    _seed(scan_root, ["x/y/1.png", "x/y/2.png"])
    client.post("/scan")
    x = client.get("/folders").json()["folders"][0]
    assert (x["name"], x["directCount"], x["totalCount"]) == ("x", 0, 2)
    assert x["children"][0]["path"] == "x/y"


def test_missing_images_are_not_counted(client, scan_root):
    _seed(scan_root, ["d/keep.png", "d/gone.png"])
    client.post("/scan")
    (scan_root / "d" / "gone.png").unlink()
    client.post("/scan")

    body = client.get("/folders").json()

    assert body["folders"][0]["totalCount"] == 1
    assert body["rootTotalCount"] == 1
    assert body["missingCount"] == 1


def test_favorite_count(client, scan_root):
    _seed(scan_root, ["1.png", "2.png"])
    client.post("/scan")
    image_id = client.get("/images").json()["items"][0]["id"]
    client.put(f"/images/{image_id}/favorite", json={"is_favorite": True})
    assert client.get("/folders").json()["favoriteCount"] == 1


def test_thumbnail_and_dot_dirs_are_excluded(client, scan_root, config):
    from app import db, repository

    _seed(scan_root, ["ok/1.png"])
    client.post("/scan")
    conn = db.connect(config.db_path)
    base = repository.get_image(conn, 1)
    for i, d in enumerate([".thumbnails", ".hidden/z", "ok/.thumbnails"]):
        repository.insert_image(conn, {
            "content_hash": f"{i:064x}", "file_path": f"{d}/f.png", "dir_path": d,
            "file_name": "f.png", "file_size": 1, "file_mtime": base.file_mtime,
            "presence": "active", "thumbnail_status": "ok", "extraction_status": "none",
            "created_at": base.created_at, "updated_at": base.updated_at,
        })
    conn.commit()
    conn.close()

    body = client.get("/folders").json()

    assert [f["name"] for f in body["folders"]] == ["ok"]
    assert body["folders"][0]["children"] == []
    assert body["rootTotalCount"] == 1


def test_build_tree_is_pure():
    folders, total = build_tree([("", 1), ("b", 2), ("a/x", 3), ("a", 1), (".thumbnails", 9)], ".thumbnails")
    assert total == 7
    assert [f["name"] for f in folders] == ["a", "b"]
    assert folders[0]["totalCount"] == 4
