import json
import shutil

from app import repository
from tests.conftest import make_png
from tests.prompt_graphs import standard, without

WORKFLOW = {"last_node_id": 9, "nodes": [{"id": 3, "type": "KSampler"}], "version": 0.4}
RAW_POSITIVE = "  masterpiece,\r\n 1girl \\(smile\\)\n\n  "
RAW_NEGATIVE = "\nlowres,  bad anatomy\t"


def _detail(client, file_name):
    items = client.get("/images", params={"include_missing": "true"}).json()["items"]
    image_id = next(i["id"] for i in items if i["fileName"] == file_name)
    return client.get(f"/images/{image_id}").json()


def test_standard_workflow_is_fully_extracted(client, scan_root):
    prompt_text = json.dumps(standard(RAW_POSITIVE, RAW_NEGATIVE), ensure_ascii=False)
    make_png(scan_root / "full.png", prompt=prompt_text, workflow=WORKFLOW)

    run = client.post("/scan").json()
    body = _detail(client, "full.png")

    assert run["extractFailedCount"] == 0
    assert body["extractionStatus"] == "full"
    gen = body["generation"]
    assert gen["positivePrompt"] == RAW_POSITIVE
    assert gen["negativePrompt"] == RAW_NEGATIVE
    assert gen["modelName"] == "sd_xl_base_1.0.safetensors"
    assert gen["seed"] == 872341905
    assert gen["steps"] == 20
    assert gen["cfg"] == 7.0
    assert gen["samplerName"] == "euler"
    assert gen["scheduler"] == "normal"
    assert (gen["genWidth"], gen["genHeight"]) == (1024, 1024)
    item = client.get("/images").json()["items"][0]
    assert item["extractionStatus"] == "full"
    assert "positivePrompt" not in item


def test_graph_without_sampler_is_partial_and_scan_completes(client, scan_root):
    make_png(scan_root / "nosampler.png", prompt=without(standard(), "3"), seed=1)
    custom = {
        "1": {"class_type": "MyCustomLoader", "inputs": {"ckpt_name": "custom.safetensors"}},
        "2": {"class_type": "MyCustomSampler", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "model": ["1", 0], "steps": 30}},
        "3": {"class_type": "MyEncode", "inputs": {"text": "custom positive"}},
        "4": {"class_type": "MyEncode", "inputs": {"text": "custom negative"}},
    }
    make_png(scan_root / "custom.png", prompt=custom, seed=2)

    run = client.post("/scan").json()

    assert run["createdCount"] == 2
    assert run["extractFailedCount"] == 2
    nosampler = _detail(client, "nosampler.png")
    assert nosampler["extractionStatus"] == "partial"
    assert all(v is None for v in nosampler["generation"].values())
    custom_body = _detail(client, "custom.png")
    assert custom_body["extractionStatus"] == "partial"
    gen = custom_body["generation"]
    assert gen["positivePrompt"] == "custom positive"
    assert gen["negativePrompt"] == "custom negative"
    assert gen["modelName"] == "custom.safetensors"
    assert gen["steps"] == 30
    assert gen["seed"] is None and gen["cfg"] is None


def test_png_without_metadata_is_none(client, scan_root):
    path = make_png(scan_root / "plain.png", size=(320, 200), mtime=1_700_000_000)

    run = client.post("/scan").json()
    body = _detail(client, "plain.png")

    assert run["extractFailedCount"] == 0
    assert body["extractionStatus"] == "none"
    assert body["fileName"] == "plain.png"
    assert body["fileSize"] == path.stat().st_size
    assert (body["imageWidth"], body["imageHeight"]) == (320, 200)
    assert body["fileMtime"] == "2023-11-14T22:13:20Z"
    assert all(v is None for v in body["generation"].values())


def test_raw_json_is_stored_verbatim(client, scan_root, config):
    from app import db

    prompt_text = json.dumps(standard(), ensure_ascii=False, indent=3)
    workflow_text = json.dumps(WORKFLOW)
    make_png(scan_root / "raw.png", prompt=prompt_text, workflow=workflow_text)
    client.post("/scan")

    conn = db.connect(config.db_path)
    raw = repository.get_raw_metadata(conn, _detail(client, "raw.png")["id"])
    conn.close()
    assert raw.prompt_json == prompt_text
    assert raw.workflow_json == workflow_text


def test_workflow_only_or_invalid_prompt_is_partial(client, scan_root):
    make_png(scan_root / "wf_only.png", workflow=WORKFLOW, seed=1)
    make_png(scan_root / "bad_json.png", prompt="{not json", seed=2)

    run = client.post("/scan").json()

    assert run["extractFailedCount"] == 2
    assert _detail(client, "wf_only.png")["extractionStatus"] == "partial"
    assert _detail(client, "bad_json.png")["extractionStatus"] == "partial"


def test_moved_file_keeps_metadata(client, scan_root, config):
    from app import db

    src = make_png(scan_root / "a" / "m.png", prompt=standard())
    client.post("/scan")
    before = _detail(client, "m.png")

    (scan_root / "b").mkdir()
    shutil.move(src, scan_root / "b" / "m.png")
    client.post("/scan")
    after = _detail(client, "m.png")

    assert after["filePath"] == "b/m.png"
    assert after["generation"] == before["generation"]
    assert after["extractionStatus"] == "full"
    conn = db.connect(config.db_path)
    assert repository.get_raw_metadata(conn, after["id"]) is not None
    conn.close()
