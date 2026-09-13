import json

from tests.conftest import make_png
from tests.prompt_graphs import standard, without

WORKFLOW = {"nodes": [{"id": 3, "type": "KSampler"}], "version": 0.4}


def _id_of(client, file_name):
    items = client.get("/images").json()["items"]
    return next(i["id"] for i in items if i["fileName"] == file_name)


def test_partial_image_exposes_raw_metadata(client, scan_root):
    prompt = without(standard(), "3")
    make_png(scan_root / "partial.png", prompt=prompt, workflow=WORKFLOW)
    client.post("/scan")
    image_id = _id_of(client, "partial.png")
    assert client.get(f"/images/{image_id}").json()["extractionStatus"] == "partial"

    resp = client.get(f"/images/{image_id}/raw-metadata")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"prompt": prompt, "workflow": WORKFLOW}


def test_prompt_only_has_null_workflow(client, scan_root):
    make_png(scan_root / "p.png", prompt=standard())
    client.post("/scan")
    body = client.get(f"/images/{_id_of(client, 'p.png')}/raw-metadata").json()
    assert body["prompt"] == standard()
    assert body["workflow"] is None


def test_unparsable_json_is_returned_as_string(client, scan_root):
    make_png(scan_root / "bad.png", prompt="{not json")
    client.post("/scan")
    body = client.get(f"/images/{_id_of(client, 'bad.png')}/raw-metadata").json()
    assert body["prompt"] == "{not json"


def test_image_without_metadata_is_404(client, scan_root):
    make_png(scan_root / "plain.png")
    client.post("/scan")
    resp = client.get(f"/images/{_id_of(client, 'plain.png')}/raw-metadata")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_unknown_image_is_404(client):
    resp = client.get("/images/424242/raw-metadata")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
