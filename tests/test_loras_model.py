from app import repository
from app.comfy_metadata import extract, extract_loras, normalize_lora_name
from tests.conftest import make_png
from tests.prompt_graphs import standard, with_loras


def test_extract_loras_from_graph():
    g = with_loras(standard(), ("detail.safetensors", 0.8, 0.7), ("styles\\anime.safetensors", 1.0, 1.0))
    usages = extract_loras(g)
    assert [u.name for u in usages] == ["detail.safetensors", "styles/anime.safetensors"]
    assert (usages[0].strength_model, usages[0].strength_clip) == (0.8, 0.7)
    # The sampler chain still resolves the checkpoint through the LoRA relays.
    assert extract(g).model_name == "sd_xl_base_1.0.safetensors"


def test_extract_loras_dedup_and_odd_values():
    g = standard()
    g["50"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "x.safetensors", "strength": 0.5}}
    g["51"] = {"class_type": "LoraLoader", "inputs": {"lora_name": "x.safetensors", "strength_model": 0.9}}
    g["52"] = {"class_type": "Weird", "inputs": {"lora_name": 123}}
    g["53"] = {"class_type": "Weird", "inputs": {"lora_name": "   "}}
    usages = extract_loras(g)
    assert len(usages) == 1
    assert usages[0].name == "x.safetensors"
    assert usages[0].strength_model == 0.5 and usages[0].strength_clip is None
    assert extract_loras({}) == []
    assert normalize_lora_name("\\sub\\a.safetensors") == "sub/a.safetensors"


def test_scan_links_images_to_loras(client, scan_root, config):
    from app import db

    make_png(scan_root / "a.png", prompt=with_loras(standard(), ("detail.safetensors", 0.8, 0.7)), seed=1)
    make_png(scan_root / "b.png", prompt=with_loras(standard(), ("detail.safetensors", 1.0, 1.0), ("style.safetensors", 0.5, 0.5)), seed=2)
    make_png(scan_root / "c.png", prompt=standard(), seed=3)
    client.post("/scan")

    conn = db.connect(config.db_path)
    loras = {l.name: l for l in repository.list_loras(conn)}
    conn.close()
    assert set(loras) == {"detail.safetensors", "style.safetensors"}
    assert loras["detail.safetensors"].image_count == 2
    assert loras["style.safetensors"].image_count == 1
    assert loras["detail.safetensors"].presence == "unknown"
    assert loras["detail.safetensors"].file_name == "detail.safetensors"

    items = {i["fileName"]: i["id"] for i in client.get("/images").json()["items"]}
    detail = client.get(f"/images/{items['a.png']}").json()
    assert detail["loras"] == [{
        "id": loras["detail.safetensors"].id, "name": "detail.safetensors", "presence": "unknown",
        "triggerWords": "", "strengthModel": 0.8, "strengthClip": 0.7,
    }]
    assert client.get(f"/images/{items['c.png']}").json()["loras"] == []

    filtered = client.get("/images", params={"lora": loras["style.safetensors"].id}).json()
    assert [i["fileName"] for i in filtered["items"]] == ["b.png"]
    assert filtered["totalCount"] == 1
    both = client.get("/images", params={"lora": loras["detail.safetensors"].id}).json()
    assert sorted(i["fileName"] for i in both["items"]) == ["a.png", "b.png"]
    assert client.get("/images", params={"lora": 9999}).json()["totalCount"] == 0


def test_image_loras_cascade_on_delete(client, scan_root, config):
    from app import db

    make_png(scan_root / "a.png", prompt=with_loras(standard(), ("d.safetensors", 1.0, 1.0)))
    client.post("/scan")
    conn = db.connect(config.db_path)
    conn.execute("DELETE FROM images")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM image_loras").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM loras").fetchone()[0] == 1  # the LoRA record itself stays
    conn.close()
