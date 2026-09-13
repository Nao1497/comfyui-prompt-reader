# comfyui-prompt-reader

ローカルに保存された ComfyUI 生成画像（PNG）を一覧・閲覧し、生成パラメータとプロンプトを確認・コピーできる管理ソフト。

仕様: [requirements.md](./requirements.md) / 設計: [design.md](./design.md) / 実装タスク: [tasks.md](./tasks.md)

## 動作環境

- Python 3.11 以上
- 依存: FastAPI, uvicorn, Pillow（開発時: pytest, httpx）

## セットアップと起動

```bash
pip install -e ".[dev]"
# config.toml の scan_root を画像フォルダの絶対パスに書き換える
python -m app            # http://127.0.0.1:8000/ を開く
python -m app path/to/other-config.toml   # 別の設定ファイルを指定する場合
```

起動後、左ペインの「スキャン実行」（または `curl -X POST http://127.0.0.1:8000/scan`）で画像を取り込む。
サムネイルは `scan_root/.thumbnails/` に WebP で生成され、DB は `data/images.db` に作成される。

- スキャン時、命名規則に合わない PNG は同じフォルダ内で `YYYYMMDDTHHMMSS_<uuid8>.png`（ファイル更新日時）にリネームされる。`rename_on_scan = false` で無効化できる。
- `lora_root` を設定して左ペインの「LoRA スキャン」を実行すると LoRA ファイルが登録され、各画像のワークフローで使われた LoRA と関連付く。LoRA ごとに Trigger Words とメモを保存できる。
- 左ペインの「タグ CSV を取り込む」で danbooru のタグ一覧 CSV（`tag, category, category_name, post_count, created_at, aliases, other_names, posts_url, wiki_url, has_wiki`）を辞書として取り込む。100 万行規模で 40 秒前後、DB は 500 MB 強になる。取り込み後はプロンプトの語が辞書と完全一致（アンダースコアと空白、大文字小文字、重み記法と括弧エスケープを正規化）で結び付き、タグで一覧を絞り込める（複数指定時は「すべて含む」と「いずれかを含む」を切替）。辞書の検索欄は全列を部分一致で引ける。
- LoRA の Trigger Words を保存すると、カンマ区切りの各語が辞書に登録され、その語を含む画像がタグで絞り込める。CSV を取り込み直しても残る。

## テスト

```bash
python -m pytest
```
