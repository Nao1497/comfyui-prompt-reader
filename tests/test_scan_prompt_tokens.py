from app import db, repository
from tests.conftest import make_png
from tests.prompt_graphs import standard

POS = "masterpiece, (long hair:1.2), hatsune miku \\(cosplay\\), <lora:x:0.8>, BREAK smile"
NEG = "lowres, (bad anatomy:1.3)"


def _conn(config):
    return db.connect(config.db_path)


def test_schema_has_dictionary_tables(config, conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    assert {"tags", "tag_aliases", "image_prompt_tokens", "image_tags", "tag_imports"} <= names
    assert {"idx_tags_post_count", "idx_image_prompt_tokens_token", "idx_image_tags_tag"} <= names
    assert db.fts_available(conn) is True  # this environment has FTS5 trigram
    db.init_schema(conn)  # idempotent


def test_tokens_are_stored_on_registration(client, scan_root, config):
    make_png(scan_root / "a.png", prompt=standard(POS, NEG))
    client.post("/scan")
    conn = _conn(config)
    image_id = conn.execute("SELECT id FROM images").fetchone()[0]
    assert repository.get_prompt_tokens(conn, image_id) == ["masterpiece", "long hair", "hatsune miku (cosplay)", "smile"]
    assert repository.get_prompt_tokens(conn, image_id, "negative") == ["lowres", "bad anatomy"]
    conn.close()


def test_image_without_prompt_has_no_tokens(client, scan_root, config):
    make_png(scan_root / "plain.png")
    client.post("/scan")
    conn = _conn(config)
    assert conn.execute("SELECT COUNT(*) FROM image_prompt_tokens").fetchone()[0] == 0
    assert repository.image_ids_without_tokens(conn) == []
    conn.close()


def test_tokens_cascade_on_delete(client, scan_root, config):
    make_png(scan_root / "a.png", prompt=standard(POS, NEG))
    client.post("/scan")
    conn = _conn(config)
    conn.execute("DELETE FROM images")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM image_prompt_tokens").fetchone()[0] == 0
    conn.close()


def test_images_without_tokens_are_detectable(client, scan_root, config):
    make_png(scan_root / "a.png", prompt=standard(POS, NEG))
    client.post("/scan")
    conn = _conn(config)
    conn.execute("DELETE FROM image_prompt_tokens")  # simulate an image registered before FR-48
    conn.commit()
    pending = repository.image_ids_without_tokens(conn)
    assert len(pending) == 1
    assert pending[0][1] == POS
    conn.close()


def test_existing_database_gets_new_tables(tmp_path):
    import sqlite3

    # Reproduce a database created before the dictionary tables existed: take the
    # current schema and drop every statement that mentions the new tables.
    keep = []
    for stmt in db.SCHEMA.split(";"):
        if not stmt.strip().startswith("CREATE"):
            continue
        if not any(t in stmt for t in ("tags", "tag_aliases", "image_prompt_tokens", "image_tags", "tag_imports")):
            keep.append(stmt)
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(";".join(keep) + ";")
    old.close()
    conn = db.open_database(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tags", "image_prompt_tokens", "image_tags"} <= names
    conn.close()
