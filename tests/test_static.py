def test_index_is_served_at_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<div id=\"grid\">" in resp.text


def test_app_js_is_served(client):
    resp = client.get("/app.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_api_routes_are_not_shadowed(client):
    assert client.get("/images").status_code == 200
    assert client.get("/folders").status_code == 200
    assert client.get("/nope").status_code == 404
    assert client.get("/nope").headers["content-type"].startswith("application/json")


def test_config_endpoint(client, config):
    body = client.get("/config").json()
    assert body == {
        "gridMinCell": config.grid_min_cell,
        "gridMaxCell": config.grid_max_cell,
        "thumbnailMaxEdge": config.thumbnail_max_edge,
        "loraRootConfigured": False,
    }
