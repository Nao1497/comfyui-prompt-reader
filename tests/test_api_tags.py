from tests.conftest import make_png
from tests.prompt_graphs import standard
from tests.tag_csv import STANDARD_ROWS, row, upload


def _names(resp):
    assert resp.status_code == 200, resp.text
    return [t["name"] for t in resp.json()["items"]]


def test_search_english_japanese_alias_and_order(client):
    upload(client)
    assert _names(client.get("/tags/search", params={"q": "long"})) == ["long_hair"]
    assert _names(client.get("/tags/search", params={"q": "ロング"})) == ["long_hair"]
    assert _names(client.get("/tags/search", params={"q": "grinning"})) == ["grin"]
    # sorted by post_count: 1girl (8.4M) before smile (4M) before blue_eyes (3M)
    top = _names(client.get("/tags/search", params={"q": "e"}))
    assert top.index("1girl") < top.index("smile") < top.index("blue_eyes")


def test_query_is_normalised_so_underscore_and_space_both_work(client):
    upload(client)
    assert _names(client.get("/tags/search", params={"q": "blue_ey"})) == ["blue_eyes"]
    assert _names(client.get("/tags/search", params={"q": "Blue Ey"})) == ["blue_eyes"]
    assert _names(client.get("/tags/search", params={"q": "miku (cos"})) == ["hatsune_miku_(cosplay)"]


def test_short_query_uses_fallback(client):
    upload(client)
    assert _names(client.get("/tags/search", params={"q": "1g"})) == ["1girl"]


def test_empty_query_returns_most_posted(client):
    upload(client)
    names = _names(client.get("/tags/search", params={"limit": 3}))
    assert names == ["1girl", "long_hair", "smile"]
    assert client.get("/tags/search", params={"limit": 0}).status_code == 422


def test_category_and_source_filters(client):
    upload(client)
    assert _names(client.get("/tags/search", params={"q": "", "category": 5})) == ["masterpiece"]
    assert _names(client.get("/tags/search", params={"q": "", "category": 4})) == ["hatsune_miku_(cosplay)"]
    assert _names(client.get("/tags/search", params={"q": "", "source": "lora"})) == []
    assert len(_names(client.get("/tags/search", params={"q": "", "source": "csv"}))) == len(STANDARD_ROWS)


def test_tag_detail_and_shape(client):
    upload(client)
    tag = next(t for t in client.get("/tags/search", params={"q": "1girl"}).json()["items"] if t["name"] == "1girl")
    body = client.get(f"/tags/{tag['id']}").json()
    assert set(body) == {
        "id", "name", "nameNormalized", "category", "categoryName", "postCount", "tagCreatedAt", "aliases",
        "otherNames", "postsUrl", "wikiUrl", "hasWiki", "source", "loraId", "loraName", "imageCount",
    }
    assert body["otherNames"] == "女の子, 少女" and body["aliases"] == "1girls"
    assert body["wikiUrl"].endswith("/wiki_pages/1girl") and body["hasWiki"] == "yes"
    assert body["imageCount"] == 0 and body["source"] == "csv" and body["loraName"] is None
    missing = client.get("/tags/999999")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_used_tags_and_image_counts(client, scan_root):
    upload(client)
    make_png(scan_root / "a.png", prompt=standard("1girl, long hair, smile", "x"), seed=1)
    make_png(scan_root / "b.png", prompt=standard("1girl, smile", "x"), seed=2)
    make_png(scan_root / "c.png", prompt=standard("1girl", "x"), seed=3)
    client.post("/scan")

    used = client.get("/tags/used").json()["items"]
    assert [(t["name"], t["imageCount"]) for t in used] == [("1girl", 3), ("smile", 2), ("long_hair", 1)]
    assert client.get("/tags/used", params={"limit": 1}).json()["items"][0]["name"] == "1girl"
    smile = next(t for t in used if t["name"] == "smile")
    assert client.get(f"/tags/{smile['id']}").json()["imageCount"] == 2

    (scan_root / "b.png").unlink()
    client.post("/scan")  # missing images no longer count
    used = {t["name"]: t["imageCount"] for t in client.get("/tags/used").json()["items"]}
    assert used == {"1girl": 2, "smile": 1, "long_hair": 1}


def test_static_routes_win_over_id_route(client):
    assert client.get("/tags/search").status_code == 200
    assert client.get("/tags/used").status_code == 200
    assert client.get("/tags/abc").status_code == 422
