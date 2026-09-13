"""
Danbooru の全タグと関連情報を API で取得し、CSV に保存するスクリプト

出力ファイル:
  danbooru_tags_detail.csv  … 詳細版（URL、別名、日本語名などを含む。Excel で開ける）
  danbooru_tags_complete.csv … tagcomplete 拡張用（tag,category,count,aliases 形式）

使い方:
    pip install requests
    python danbooru_tags.py
"""
import csv
import time
from collections import defaultdict
from urllib.parse import quote

import requests

BASE = "https://danbooru.donmai.us"
HEADERS = {"User-Agent": "tag-list-downloader/1.0 (personal use)"}
MIN_COUNT = 1       # 使用数がこの値以上のタグだけ取得（実用なら 10〜50 程度がおすすめ）
GET_ALIASES = True  # 別名（エイリアス）を取得する
GET_WIKI = True     # Wiki の別表記（日本語名など）を取得する
WAIT = 1.0          # リクエスト間隔（秒）。サーバーに負荷をかけないため短くしすぎないこと

CATEGORY_NAMES = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}


def fetch(path, params, retries=5):
    for i in range(retries):
        try:
            r = requests.get(BASE + path, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
            print(f"  HTTP {r.status_code}、{5 * (i + 1)} 秒後に再試行")
        except requests.RequestException as e:
            print(f"  通信エラー: {e}、{5 * (i + 1)} 秒後に再試行")
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"{path} の取得に失敗しました")


def fetch_all(path, params, label):
    """id の大きい順に 1000 件ずつ、最後まで取得する"""
    results = []
    cursor = "b999999999"
    while True:
        data = fetch(path, {**params, "limit": 1000, "page": cursor})
        if not data:
            break
        results.extend(data)
        cursor = f"b{min(d['id'] for d in data)}"
        print(f"[{label}] {len(results)} 件取得")
        time.sleep(WAIT)
    return results


def main():
    # 1. タグ本体
    tags = fetch_all("/tags.json", {
        "search[post_count]": f">={MIN_COUNT}",
        "only": "id,name,category,post_count,created_at",
    }, "タグ")

    # 2. 別名（正式タグ名 → 別名のリスト）
    aliases = defaultdict(list)
    if GET_ALIASES:
        for a in fetch_all("/tag_aliases.json", {
            "search[status]": "active",
            "only": "id,antecedent_name,consequent_name",
        }, "別名"):
            aliases[a["consequent_name"]].append(a["antecedent_name"])

    # 3. Wiki の別表記（日本語名など）
    other_names = {}
    if GET_WIKI:
        for w in fetch_all("/wiki_pages.json", {
            "search[is_deleted]": "false",
            "only": "id,title,other_names",
        }, "Wiki"):
            other_names[w["title"]] = w.get("other_names") or []

    tags.sort(key=lambda t: t["post_count"], reverse=True)

    # 詳細版（utf-8-sig なので Excel でも文字化けしない）
    with open("danbooru_tags_detail.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tag", "category", "category_name", "post_count", "created_at",
                    "aliases", "other_names", "posts_url", "wiki_url", "has_wiki"])
        for t in tags:
            name = t["name"]
            q = quote(name, safe="")
            w.writerow([
                name,
                t["category"],
                CATEGORY_NAMES.get(t["category"], ""),
                t["post_count"],
                (t.get("created_at") or "")[:10],
                ", ".join(aliases.get(name, [])),
                ", ".join(other_names.get(name, [])),
                f"{BASE}/posts?tags={q}",
                f"{BASE}/wiki_pages/{q}",
                "yes" if name in other_names else "no",
            ])

    # tagcomplete 用
    with open("danbooru_tags_complete.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for t in tags:
            w.writerow([t["name"], t["category"], t["post_count"],
                        ",".join(aliases.get(t["name"], []))])

    print(f"完了: {len(tags)} 件のタグを保存しました")


if __name__ == "__main__":
    main()
