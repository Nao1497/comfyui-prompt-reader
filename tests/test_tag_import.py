from app import db, repository
from tests.conftest import make_png
from tests.prompt_graphs import standard
from tests.tag_csv import STANDARD_ROWS, csv_text, row, upload

IMPORT_KEYS = {"tagImportId", "startedAt", "finishedAt", "fileName", "readCount", "importedCount",
               "skippedCount", "aliasCount", "linkedImageCount"}


def _conn(config):
    return db.connect(config.db_path)


def _tag_ids(conn, *names):
    return [int(conn.execute("SELECT id FROM tags WHERE name = ?", (n,)).fetchone()[0]) for n in names]


def test_import_replaces_csv_rows_without_duplicating(client, config):
    first = upload(client)
    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) == IMPORT_KEYS
    assert body["readCount"] == len(STANDARD_ROWS)
    assert body["importedCount"] == len(STANDARD_ROWS)
    assert body["skippedCount"] == 0
    assert body["fileName"] == "tags.csv"
    # aliases: 1girls, longhair, blueeyes, blue_eye, grinning (smile is a canonical name -> excluded)
    assert body["aliasCount"] == 5

    second = upload(client).json()
    conn = _conn(config)
    assert repository.count_tags(conn) == len(STANDARD_ROWS)
    assert conn.execute("SELECT COUNT(*) FROM tag_imports").fetchone()[0] == 2
    tag = conn.execute("SELECT * FROM tags WHERE name = '1girl'").fetchone()
    assert tag["name_normalized"] == "1girl"
    assert tag["category"] == 0 and tag["category_name"] == "general"
    assert tag["post_count"] == 8411385
    assert tag["other_names"] == "女の子, 少女"
    assert tag["has_wiki"] == "yes" and tag["wiki_url"].endswith("/1girl")
    assert tag["source"] == "csv" and tag["tag_created_at"] == "2013-02-27"
    conn.close()
    assert second["importedCount"] == len(STANDARD_ROWS)


def test_invalid_header_is_rejected_without_changes(client, config):
    upload(client)
    bad = upload(client, text="tag,category\nfoo,0\n")
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_TAG_CSV"
    assert "post_count" in bad.json()["error"]["message"]
    conn = _conn(config)
    assert repository.count_tags(conn) == len(STANDARD_ROWS)
    conn.close()


def test_bad_rows_are_skipped_and_counted(client, config):
    rows = STANDARD_ROWS + [
        row("", 0, 1),                     # empty tag
        row("no_count", 0, "abc"),         # unparsable post_count
        row("no_cat", "", 5),              # unparsable category
        row("1girl", 0, 1),                # duplicate within the CSV
    ]
    body = upload(client, rows).json()
    assert body["readCount"] == len(rows)
    assert body["importedCount"] == len(STANDARD_ROWS)
    assert body["skippedCount"] == 4


def test_bom_and_windows_newlines(client, config):
    text = "﻿" + csv_text(STANDARD_ROWS).replace("\n", "\r\n")
    assert upload(client, text=text).json()["importedCount"] == len(STANDARD_ROWS)


def test_weighted_and_escaped_words_link_to_tags(client, scan_root, config):
    upload(client)
    prompt = "masterpiece, (long hair:1.2), hatsune miku \\(cosplay\\), (((smile))), <lora:x:1>"
    make_png(scan_root / "a.png", prompt=standard(prompt, "lowres, (bad anatomy:1.3)"))
    client.post("/scan")

    conn = _conn(config)
    image_id = conn.execute("SELECT id FROM images").fetchone()[0]
    expected = _tag_ids(conn, "masterpiece", "long_hair", "hatsune_miku_(cosplay)", "smile")
    assert repository.get_image_tag_ids(conn, image_id) == sorted(expected)
    conn.close()


def test_alias_matches_and_canonical_wins(client, scan_root, config):
    upload(client)
    # "1girls" is an alias of 1girl; "blue eye" an alias of blue_eyes; "smile" is canonical AND an alias of grin.
    make_png(scan_root / "a.png", prompt=standard("1girls, blue eye, smile, 女の子", "x"))
    client.post("/scan")
    conn = _conn(config)
    image_id = conn.execute("SELECT id FROM images").fetchone()[0]
    one_girl, blue_eyes, smile, grin = _tag_ids(conn, "1girl", "blue_eyes", "smile", "grin")
    linked = repository.get_image_tag_ids(conn, image_id)
    assert set(linked) == {one_girl, blue_eyes, smile}
    assert grin not in linked                      # canonical "smile" beats alias-of-grin
    assert "女の子" in repository.get_prompt_tokens(conn, image_id)  # kept as a word, but no tag
    conn.close()


def test_negative_words_are_not_linked(client, scan_root, config):
    upload(client)
    make_png(scan_root / "a.png", prompt=standard("masterpiece", "bad anatomy, long hair"))
    client.post("/scan")
    conn = _conn(config)
    image_id = conn.execute("SELECT id FROM images").fetchone()[0]
    assert repository.get_image_tag_ids(conn, image_id) == _tag_ids(conn, "masterpiece")
    assert repository.get_prompt_tokens(conn, image_id, "negative") == ["bad anatomy", "long hair"]
    conn.close()


def test_import_after_scan_links_existing_images(client, scan_root, config):
    make_png(scan_root / "a.png", prompt=standard("masterpiece, long hair", "x"), seed=1)
    make_png(scan_root / "b.png", prompt=standard("nothing known here", "x"), seed=2)
    client.post("/scan")
    conn = _conn(config)
    assert conn.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0] == 0
    conn.execute("DELETE FROM image_prompt_tokens")  # as if registered before FR-48
    conn.commit()
    conn.close()

    body = upload(client).json()

    assert body["linkedImageCount"] == 1
    conn = _conn(config)
    a = conn.execute("SELECT id FROM images WHERE file_name = 'a.png'").fetchone()[0]
    assert repository.get_image_tag_ids(conn, a) == sorted(_tag_ids(conn, "masterpiece", "long_hair"))
    assert repository.get_prompt_tokens(conn, a) == ["masterpiece", "long hair"]
    conn.close()


def test_lora_rows_survive_and_csv_takes_over_on_collision(client, scan_root, config):
    conn = _conn(config)
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO tags (name, name_normalized, category, category_name, post_count, source, lora_id, created_at, updated_at)"
        " VALUES ('sksartstyle', 'sksartstyle', NULL, 'lora', 0, 'lora', NULL, ?, ?)", (now, now))
    conn.execute(
        "INSERT INTO tags (name, name_normalized, category, category_name, post_count, source, lora_id, created_at, updated_at)"
        " VALUES ('Long Hair', 'long hair', NULL, 'lora', 0, 'lora', NULL, ?, ?)", (now, now))
    conn.commit()
    lora_row_id = conn.execute("SELECT id FROM tags WHERE name = 'Long Hair'").fetchone()[0]
    conn.close()

    body = upload(client).json()

    conn = _conn(config)
    assert body["importedCount"] == len(STANDARD_ROWS)
    kept = conn.execute("SELECT source FROM tags WHERE name = 'sksartstyle'").fetchone()
    assert kept["source"] == "lora"
    taken = conn.execute("SELECT id, name, source, post_count FROM tags WHERE name_normalized = 'long hair'").fetchone()
    assert taken["id"] == lora_row_id           # same row, now owned by the CSV
    assert taken["name"] == "long_hair" and taken["source"] == "csv" and taken["post_count"] == 5123456
    assert repository.count_tags(conn) == len(STANDARD_ROWS) + 1
    conn.close()


def test_import_is_rejected_while_scanning(client, monkeypatch):
    import threading

    from app import scanner

    started, release = threading.Event(), threading.Event()
    real = scanner.run_scan

    def slow(config, conn):
        started.set(); release.wait(5); return real(config, conn)

    monkeypatch.setattr(scanner, "run_scan", slow)
    t = threading.Thread(target=lambda: client.post("/scan"))
    t.start(); started.wait(5)
    resp = upload(client)
    release.set(); t.join(10)
    assert resp.status_code == 409
