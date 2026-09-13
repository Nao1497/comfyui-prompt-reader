from tests.conftest import make_png
from tests.prompt_graphs import standard, with_loras
from tests.tag_csv import upload


def _lora_id(client):
    return client.get("/loras").json()["items"][0]["id"]


def _search(client, **params):
    return client.get("/tags/search", params=params).json()["items"]


def _files(client, **params):
    return sorted(i["fileName"] for i in client.get("/images", params=params).json()["items"])


def test_trigger_words_are_registered_and_linked_without_rescan(client, scan_root):
    make_png(scan_root / "a.png", prompt=with_loras(standard("sksartstyle, 1girl, (by artist zed:1.1)", "x"), ("s.safetensors", 1, 1)), seed=1)
    make_png(scan_root / "b.png", prompt=standard("1girl", "sksartstyle"), seed=2)  # negative only
    client.post("/scan")
    lora_id = _lora_id(client)
    assert _search(client, q="sks") == []

    resp = client.put(f"/loras/{lora_id}", json={"trigger_words": "sksartstyle, by artist zed,  ,"})
    assert resp.status_code == 200

    tags = {t["name"]: t for t in _search(client, q="", source="lora")}
    assert set(tags) == {"sksartstyle", "by artist zed"}
    for t in tags.values():
        assert t["source"] == "lora" and t["loraId"] == lora_id and t["category"] is None
        assert t["categoryName"] == "lora"
    assert tags["sksartstyle"]["imageCount"] == 1
    assert _files(client, tag=tags["sksartstyle"]["id"]) == ["a.png"]         # AC-38: no rescan
    assert _files(client, tag=tags["by artist zed"]["id"]) == ["a.png"]       # weight stripped on both sides
    detail = client.get(f"/tags/{tags['sksartstyle']['id']}").json()
    assert detail["loraName"] == "s.safetensors"
    # searchable through the same index as CSV tags
    assert [t["name"] for t in _search(client, q="artist zed")] == ["by artist zed"]


def test_registration_survives_csv_import_and_does_not_duplicate(client, scan_root):
    make_png(scan_root / "a.png", prompt=standard("sksartstyle, long hair", "x"))
    client.post("/scan")
    upload(client)
    lora_conn_id = None
    # register a LoRA by scanning workflows: use a png with a LoraLoader
    make_png(scan_root / "l.png", prompt=with_loras(standard("x", "y"), ("t.safetensors", 1, 1)), seed=3)
    client.post("/scan")
    lora_id = _lora_id(client)

    client.put(f"/loras/{lora_id}", json={"trigger_words": "sksartstyle, long_hair, Long Hair"})
    before = {t["name"]: t for t in _search(client, q="", limit=200)}
    assert before["sksartstyle"]["source"] == "lora"
    assert before["long_hair"]["source"] == "csv"                 # existing CSV word: untouched
    assert sum(1 for n in before if n.lower().replace("_", " ") == "long hair") == 1
    sks_id = before["sksartstyle"]["id"]

    upload(client)  # CSV re-import (AC-36)
    after = {t["name"]: t for t in _search(client, q="", limit=200)}
    assert after["sksartstyle"]["id"] == sks_id and after["sksartstyle"]["source"] == "lora"
    assert _files(client, tag=sks_id) == ["a.png"]

    # saving the same words again adds nothing; removing a word deletes nothing
    client.put(f"/loras/{lora_id}", json={"trigger_words": "sksartstyle"})
    client.put(f"/loras/{lora_id}", json={"trigger_words": ""})
    assert {t["name"] for t in _search(client, q="", source="lora")} == {"sksartstyle"}
    assert _files(client, tag=sks_id) == ["a.png"]


def test_prompt_tokens_show_lora_source(client, scan_root):
    make_png(scan_root / "a.png", prompt=with_loras(standard("sksartstyle, 1girl", "x"), ("s.safetensors", 1, 1)))
    client.post("/scan")
    lora_id = _lora_id(client)
    client.put(f"/loras/{lora_id}", json={"trigger_words": "sksartstyle"})
    image_id = client.get("/images").json()["items"][0]["id"]
    tokens = client.get(f"/images/{image_id}").json()["promptTokens"]
    assert tokens[0]["token"] == "sksartstyle"
    assert tokens[0]["tag"]["source"] == "lora" and tokens[0]["tag"]["loraId"] == lora_id
    assert tokens[1]["tag"] is None  # no CSV imported in this test


def test_memo_only_update_registers_nothing(client, scan_root):
    make_png(scan_root / "a.png", prompt=with_loras(standard("x", "y"), ("s.safetensors", 1, 1)))
    client.post("/scan")
    client.put(f"/loras/{_lora_id(client)}", json={"memo": "note, with, commas"})
    assert _search(client, q="", source="lora") == []
