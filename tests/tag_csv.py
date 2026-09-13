"""Build danbooru-style tag CSV text for tests."""

from __future__ import annotations

import csv
import io

HEADER = ["tag", "category", "category_name", "post_count", "created_at", "aliases",
          "other_names", "posts_url", "wiki_url", "has_wiki"]
CATS = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}


def row(tag, category=0, post_count=100, aliases="", other_names="", has_wiki="no"):
    return [tag, category, CATS.get(category, "general"), post_count, "2013-02-27", aliases, other_names,
            f"https://danbooru.donmai.us/posts?tags={tag}",
            f"https://danbooru.donmai.us/wiki_pages/{tag}" if has_wiki == "yes" else "", has_wiki]


def csv_text(rows, header=HEADER):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


STANDARD_ROWS = [
    row("1girl", 0, 8411385, aliases="1girls", other_names="女の子, 少女", has_wiki="yes"),
    row("long_hair", 0, 5123456, aliases="longhair", other_names="ロングヘア, 長髪"),
    row("masterpiece", 5, 812345),
    row("hatsune_miku_(cosplay)", 4, 12000, other_names="初音ミク（コスプレ）"),
    row("smile", 0, 4000000, other_names="笑顔"),
    row("bad_anatomy", 0, 90000),
    row("blue_eyes", 0, 3000000, aliases="blueeyes,blue_eye"),
    # "smile" is also listed as an alias of this tag: canonical "smile" must win (FR-47).
    row("grin", 0, 500000, aliases="smile,grinning"),
]


def upload(client, rows=STANDARD_ROWS, name="tags.csv", text=None):
    data = text if text is not None else csv_text(rows)
    return client.post("/tags/import", files={"file": (name, data.encode("utf-8"), "text/csv")})
