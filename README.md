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

## テスト

```bash
python -m pytest
```
