# tasks.md

入力: [requirements.md](./requirements.md) / [design.md](./design.md)

## 前提

- 本書は実装タスクの分割のみを定義する。仕様の追加・変更は行わない。
- requirements.md「7. 保留事項」（全文検索、並び順切り替え、タグ付け・モデル名等での絞り込み、ComfyUI 以外の形式、ワークフロー再利用）はタスクに含めない。
- 仕様が不明確、または両ファイルが矛盾している箇所は実装方針を決めず、末尾「確認事項」に列挙した。該当タスクの完了条件には `→ 確認事項 #n` と記す。確認事項の回答が出るまで、その箇所は回答を待つ（他の部分は先に進めてよい）。
- 検証手段（製品仕様ではなく開発上の前提）:
  - テストは `pytest` を使用し `tests/` 配下に置く。API は FastAPI の `TestClient`（httpx）で叩く。
  - テスト用 PNG は Pillow で生成し、ComfyUI メタデータは `PngInfo.add_text("prompt", ...)` / `add_text("workflow", ...)` で tEXt チャンクに埋め込む（design §4 の入力形式と同じ）。
  - テストでは `tmp_path` をコピーした `scan_root` と `db_path` を持つ `AppConfig` を直接生成して使う。
- 各タスクは 1 コミット相当。完了条件の検証コマンドがすべて通った時点で完了とする。

## フェーズ構成

| フェーズ | 内容 | タスク |
|---|---|---|
| 1 | 最小構成: 1 フォルダをスキャンして一覧と詳細が返る（メタデータ抽出・サムネイルなし） | TASK-1 〜 TASK-6 |
| 2 | 再スキャン（パス更新 / missing）とお気に入り | TASK-7 〜 TASK-9 |
| 3 | ComfyUI メタデータ抽出 | TASK-10 〜 TASK-13 |
| 4 | サムネイル生成・配信、原寸配信 | TASK-14 〜 TASK-16 |
| 5 | フォルダツリーとフォルダ絞り込み | TASK-17 〜 TASK-18 |
| 6 | フロントエンド（API 完成後） | TASK-19 〜 TASK-22 |

---

## フェーズ 1: 最小構成（スキャン → 一覧 → 詳細）

### TASK-1

- ID: TASK-1
- 目的: パッケージ骨格と `config.toml` の読み込みを用意し、`python -m app` の起点を作る
- 対象ファイル:
  - 新規: `pyproject.toml`（依存: fastapi, uvicorn, pillow / 開発: pytest, httpx）
  - 新規: `app/__init__.py`, `app/config.py`
  - 新規: `config.toml`（サンプル値。`scan_root` は利用者環境に合わせて書き換える前提）
  - 新規: `.gitignore`（`data/`, `__pycache__/` 等）
  - 新規: `tests/__init__.py`, `tests/test_config.py`
- 完了条件:
  - `app.config.load_config(path) -> AppConfig` が design §1「設定」の全項目（`scan_root`, `thumbnail_dir_name`, `thumbnail_max_edge`, `thumbnail_quality`, `grid_min_cell`, `grid_max_cell`, `db_path`, `host`, `port`）を持つ dataclass を返す。
  - 未記載項目は design §1 の既定値（`.thumbnails` / 768 / 80 / 96 / 320 / `./data/images.db` / `127.0.0.1` / 8000）で埋まる。
  - `scan_root` は既定値を持たないため、未記載なら明示的な例外を送出する。
  - `db_path` の相対パスの基準は → 確認事項 #15。
  - 検証: `pytest tests/test_config.py`（既定値補完、全項目指定、`scan_root` 欠落の 3 ケース）
- 関連: design §1「設定」「ディレクトリ構成」（直接対応する FR / AC なし）

### TASK-2

- ID: TASK-2
- 目的: SQLite スキーマ（`images` / `image_raw_metadata` / `scan_runs`）と接続初期化を実装する
- 対象ファイル:
  - 新規: `app/db.py`（接続、PRAGMA 設定、スキーマ初期化）
  - 新規: `app/models.py`（`Image`, `RawMetadata`, `ScanRun` dataclass）
  - 新規: `tests/test_db.py`
- 完了条件:
  - `db.connect(db_path)` が親ディレクトリを作成して接続し、`PRAGMA journal_mode = WAL`、`PRAGMA foreign_keys = ON` を設定する。
  - `db.init_schema(conn)` が design §2 の 3 テーブルと索引（`uk_images_content_hash`, `idx_images_cursor`, `idx_images_favorite`, `idx_images_dir`, `idx_images_presence`）を `CREATE ... IF NOT EXISTS` で作成し、2 回呼んでもエラーにならない。
  - `image_raw_metadata.image_id` は `images.id` への外部キーで `ON DELETE CASCADE`。
  - 検証: `pytest tests/test_db.py`
    - `PRAGMA journal_mode` が `wal`、`PRAGMA foreign_keys` が 1 を返す
    - `images` 行削除で対応する `image_raw_metadata` 行が消える
    - 同一 `content_hash` の二重 INSERT が `IntegrityError` になる
    - `sqlite_master` に 3 テーブルと 5 索引が存在する
- 関連: design §2（FR-3 の一意制約、FR-5 のレコード保持の基盤）

### TASK-3

- ID: TASK-3
- 目的: フォルダ再帰走査・ハッシュ算出・ファイル情報のみでの新規登録を行う最小スキャナを実装する
- 対象ファイル:
  - 新規: `app/scanner.py`（`run_scan(config, conn) -> ScanRun`）
  - 新規: `app/repository.py`（`find_image_by_hash`, `insert_image`, `set_presence`, `insert_scan_run`, `count_images`）
  - 新規: `tests/conftest.py`（テスト用 PNG 生成ヘルパ、`AppConfig` / 接続のフィクスチャ）
  - 新規: `tests/test_scanner_basic.py`
- 完了条件:
  - `scan_root` が存在しない場合は例外（例: `ScanRootNotFound`）を送出し、DB に一切書き込まない。
  - `scan_root/<thumbnail_dir_name>` を作成する（存在すれば何もしない）。
  - `scan_root` 配下を再帰走査し、拡張子 `.png`（大文字小文字不問）を列挙する。`<thumbnail_dir_name>` 配下と `.` で始まるディレクトリは除外する。
  - 各ファイルの SHA-256 を 64KB 単位のストリーム読み込みで算出する。
  - 未登録ハッシュ → Pillow で開き `image_width` / `image_height` を取得し、`file_path` / `dir_path`（`scan_root` からの相対、区切りは `/`、ルート直下は空文字列）/ `file_name` / `file_size` / `file_mtime`（UTC ISO8601。精度 → 確認事項 #18）/ `presence='active'` / `is_favorite=0` / `thumbnail_status='pending'` / `extraction_status='none'`（暫定。TASK-12 で実装に置き換える）/ `created_at` / `updated_at` を登録する。
  - 登録済みかつ `file_path` 一致 → `presence='active'` に更新するのみ（パス不一致と missing 判定は TASK-7）。
  - Pillow で開けないファイルはスキャンを止めず次へ進む（登録内容は → 確認事項 #7）。
  - `scan_runs` に `started_at` / `finished_at` / `scanned_count` / `created_count` を記録する（他の件数は 0）。
  - 検証: `pytest tests/test_scanner_basic.py`
    - ルート直下 / `sub/` / `sub/deep/` に PNG を置きスキャン → 全件登録され `dir_path` がそれぞれ `""` / `sub` / `sub/deep` になる（AC-1）
    - 同じフォルダを 2 回スキャン → 2 回目の `created_count` が 0、`images` 件数が不変（AC-2）
    - `.thumbnails/x.png` と `.hidden/y.png` を置いても登録されない（AC-19 の除外部分、FR-32）
    - 同一内容のファイルを 2 か所に置いた場合の扱い → 確認事項 #6
    - 登録行の `file_name` / `file_size` / `image_width` / `image_height` / `file_mtime` が実ファイルと一致する（AC-9 のファイル情報部分）
- 関連: FR-1, FR-3, FR-13, FR-24, FR-32 / AC-1, AC-2, AC-9（ファイル情報部分）, AC-19（除外部分）

### TASK-4

- ID: TASK-4
- 目的: FastAPI アプリ骨格・エラー応答形式の統一・起動エントリポイントを実装する
- 対象ファイル:
  - 新規: `app/api.py`（`create_app(config) -> FastAPI`、例外ハンドラ）
  - 新規: `app/__main__.py`（`config.toml` 読み込み → `uvicorn.run(app, host, port)`）
  - 新規: `tests/test_app_errors.py`
- 完了条件:
  - `create_app(config)` がテストから `AppConfig` を注入できる形で `FastAPI` を返し、lifespan で `db.connect` / `init_schema` を実行する。
  - 以下すべてが `{"error": {"code": ..., "message": ...}}` の JSON かつ `Content-Type: application/json` で返る:
    - アプリ内で送出する API エラー（`NOT_FOUND` / `INVALID_SCAN_ROOT` / `SCAN_IN_PROGRESS` / `FILE_MISSING` / `THUMBNAIL_UNAVAILABLE` / `VALIDATION_ERROR` を表す例外型）
    - FastAPI の `RequestValidationError`（`code=VALIDATION_ERROR`。HTTP ステータス → 確認事項 #9）
    - 未定義ルートの 404
  - `python -m app` でアプリケーションルートの `config.toml` を読み込み、`host` / `port` で uvicorn が起動する。
  - 検証:
    - `pytest tests/test_app_errors.py`（未定義ルート、バリデーションエラー、独自エラーの 3 ケースで形式と Content-Type を確認）
    - `python -m app` を起動し `curl -i http://127.0.0.1:8000/no-such-route` で JSON エラーが返る
- 関連: FR-22, FR-23 / AC-15（基盤。詳細取得での確認は TASK-6）

### TASK-5

- ID: TASK-5
- 目的: `POST /scan` を実装し、スキャン結果を JSON で返す
- 対象ファイル:
  - 変更: `app/api.py`
  - 変更: `app/scanner.py`（同時実行防止のロック）
  - 新規: `tests/test_api_scan.py`
- 完了条件:
  - `POST /scan` が TASK-3 の `run_scan` を実行し、design §7 の形式（`scanRunId`, `startedAt`, `finishedAt`, `scannedCount`, `createdCount`, `updatedCount`, `missingCount`, `extractFailedCount`, `thumbnailGeneratedCount`, `thumbnailFailedCount`）で 200 を返す。
  - `scan_root` 不在 → `400 INVALID_SCAN_ROOT`。DB は一切変化しない。
  - 実行中の再要求 → `409 SCAN_IN_PROGRESS`。スキャン実行中も他リクエストを受け付けられる（同期関数エンドポイントでスレッドプール実行し、`threading.Lock` の非ブロッキング取得で判定する）。
  - 検証: `pytest tests/test_api_scan.py`
    - 有効な `scan_root` → 200 で上記キーがすべて存在する
    - 一度スキャン後に `scan_root` を存在しないパスに差し替えた `AppConfig` で `POST /scan` → 400、`images` / `scan_runs` の行数と内容が不変（AC-16）
    - `run_scan` を遅延するスタブに差し替え、別スレッドから 2 回要求 → 一方が 409
- 関連: FR-2, FR-6, FR-24 / AC-16, AC-5（返却形式。`updatedCount` / `missingCount` の値は TASK-7、`extractFailedCount` は TASK-12、サムネイル件数は TASK-15 で有効になる）

### TASK-6

- ID: TASK-6
- 目的: `GET /images`（カーソル逐次取得・総件数）と `GET /images/{id}` を実装し、最小構成を端から端まで通す
- 対象ファイル:
  - 変更: `app/api.py`
  - 変更: `app/repository.py`（`list_images(cursor, limit)`, `count_images()`, `get_image(id)`）
  - 新規: `tests/test_api_images.py`
- 完了条件:
  - `GET /images`:
    - query `cursor`（省略可）, `limit`（既定 100、最大 300。超過時の扱い → 確認事項 #9）
    - 並び順は `file_mtime DESC, id DESC` 固定（`file_mtime` の精度 → 確認事項 #18）
    - カーソルは `<file_mtime>|<id>` を URL-safe Base64 にした文字列。続きは行値比較 `(file_mtime, id) < (:mtime, :id)` で取得する。復号不能なカーソルは `VALIDATION_ERROR`
    - `totalCount` は `cursor` 未指定時のみ数値、以降は `null`
    - 返却件数が `limit` 未満なら `nextCursor` は `null`
    - `items` は design §7 の項目のみ（`positivePrompt` / `negativePrompt` / 生 JSON を含まない）。`thumbnailUrl` は `/images/{id}/thumbnail`
  - `GET /images/{id}`: design §7 の形式。`generation` の各キーは未取得でも `null` で返し省略しない（この時点では全項目 `null`）。未存在 ID → `404 NOT_FOUND`
  - 検証: `pytest tests/test_api_images.py`
    - `os.utime` で異なる更新日時を付けた PNG をスキャン → `fileMtime` 降順で返る。同一日時は `id` 降順（AC-10）
    - 7 件登録し `limit=3` で `nextCursor` が `null` になるまで辿る → ID に重複がなく、合計件数が初回の `totalCount` と一致する（AC-26）
    - 2 ページ目以降の `totalCount` が `null`
    - 存在しない ID → 404、本文が `{"error":{"code":"NOT_FOUND",...}}`、`Content-Type` が `application/json`（AC-15）
    - `python -m app` 起動後、`curl -X POST :8000/scan` → `curl ":8000/images?limit=5"` → `curl :8000/images/1` が順に動く（フェーズ 1 の到達確認）
- 関連: FR-14, FR-17, FR-22, FR-23, FR-37, FR-38 / AC-10, AC-15, AC-26

---

## フェーズ 2: 再スキャンとお気に入り

### TASK-7

- ID: TASK-7
- 目的: 再スキャン時のパス更新・missing 判定・件数集計を実装する
- 対象ファイル:
  - 変更: `app/scanner.py`
  - 変更: `app/repository.py`（`update_image_path`, `mark_missing_except`, `insert_scan_run` の件数追加）
  - 新規: `tests/test_scanner_rescan.py`
- 完了条件:
  - 登録済みかつ `file_path` 不一致 → `file_path` / `file_name` / `file_mtime` / `presence` を更新する（`dir_path` の更新 → 確認事項 #5）。`is_favorite`、抽出済みメタデータ列、`image_raw_metadata`、`thumbnail_name` は変更しない。
  - 全ファイル処理後、今回の走査で出現しなかった `presence='active'` のレコードを `missing` に更新する。レコードと `is_favorite` は削除・変更しない。
  - missing だったファイルが再出現した場合は `active` に戻る。
  - `scan_runs.updated_count` / `missing_count` を記録する（定義 → 確認事項 #4）。
  - 検証: `pytest tests/test_scanner_rescan.py`
    - スキャン → `repository` で `is_favorite=1` に設定 → ファイルを別フォルダへ移動 → 再スキャン: 件数不変、`file_path` / `file_name` が新しい位置、`is_favorite` が 1 のまま（AC-3）
    - スキャン → `is_favorite=1` → ファイル削除 → 再スキャン: レコードが残り `presence='missing'`、`is_favorite` が 1 のまま（AC-4）
    - 削除したファイルを元に戻して再スキャン → `presence='active'`
    - `POST /scan` の応答で `scannedCount` / `createdCount` / `updatedCount` / `missingCount` / `extractFailedCount` が上記操作に対応した値になる（AC-5。`extractFailedCount` の値は TASK-12 まで 0）
- 関連: FR-4, FR-5, FR-6 / AC-3, AC-4, AC-5

### TASK-8

- ID: TASK-8
- 目的: `PUT /images/{id}/favorite` を実装する
- 対象ファイル:
  - 変更: `app/api.py`
  - 変更: `app/repository.py`（`set_favorite`）
  - 新規: `tests/test_api_favorite.py`
- 完了条件:
  - request body `{"is_favorite": true|false}`、応答 `{"id": ..., "isFavorite": ...}` で 200。
  - `images.is_favorite` と `updated_at` のみ更新する。画像ファイルおよび PNG メタデータには一切書き込まない。
  - 冪等。同じ値を繰り返し送っても 200。
  - 未存在 ID → `404 NOT_FOUND`。body 不正（キー欠落、真偽値以外）→ `VALIDATION_ERROR`。
  - 検証: `pytest tests/test_api_favorite.py`
    - オン → DB の `is_favorite=1`、オフ → 0、同値を 2 回送っても 200
    - 操作前後で画像ファイルの SHA-256 と `st_mtime` が変化しない（AC-13）
    - `GET /images/{id}` の `isFavorite` が追従する
- 関連: FR-19, FR-20, FR-21 / AC-13

### TASK-9

- ID: TASK-9
- 目的: `GET /images` に `favorite_only` / `include_missing` の絞り込みを追加する
- 対象ファイル:
  - 変更: `app/api.py`
  - 変更: `app/repository.py`（`list_images` / `count_images` に条件追加）
  - 変更: `tests/test_api_images.py`
- 完了条件:
  - `favorite_only=true` → `is_favorite=1` のみ。既定 false。
  - `include_missing=false`（既定）→ `presence='active'` のみ。`true` → missing も含む。
  - `totalCount` は同一条件の件数。カーソル逐次取得と併用できる。
  - 検証: `pytest tests/test_api_images.py`
    - お気に入りをオンにした画像が `favorite_only=true` の一覧に含まれる（AC-11）
    - オフにすると `favorite_only=true` の一覧から外れる（AC-12）
    - ファイル削除 → 再スキャン後、既定の一覧に missing 画像が含まれず、`include_missing=true` のときのみ含まれる。`totalCount` も同様に変わる（AC-14）
- 関連: FR-15, FR-16, FR-38 / AC-11, AC-12, AC-14

---

## フェーズ 3: ComfyUI メタデータ抽出

### TASK-10

- ID: TASK-10
- 目的: `prompt` グラフ解析の基盤（リンク解決、サンプラー特定、生成パラメータ、モデル名、生成解像度、抽出状態判定）を純粋関数として実装する
- 対象ファイル:
  - 新規: `app/comfy_metadata.py`（`extract(prompt: dict) -> ExtractedMetadata`）
  - 新規: `tests/fixtures/prompts/*.json`（下記ケースの `prompt` JSON）
  - 新規: `tests/test_comfy_metadata.py`
- 完了条件:
  - `ExtractedMetadata` は `positive_prompt`, `negative_prompt`, `model_name`, `seed`, `steps`, `cfg`, `sampler_name`, `scheduler`, `gen_width`, `gen_height`, `status`（`full` / `partial`）を持つ。未取得は `None`。
  - `resolve(value)`: `[ノードID, 出力インデックス]` の 2 要素配列はリンクとして参照先ノードを返し、それ以外はリテラルを返す。深さ上限 32、訪問済み集合で循環を防ぎ、上限到達時はその項目を未取得にする。
  - サンプラー特定: `inputs` に `positive` と `negative` の両方を持つノードを候補とする（`class_type` では判定しない）。0 件 → 全項目未取得で `partial`。1 件 → 採用。複数 → `SaveImage` / `PreviewImage` / `class_type` に `SaveImage` を含むノードの `images` 入力を逆向きに辿り最初に到達したものを採用。到達不能ならノード ID 数値昇順の最小。
  - `seed` / `steps` / `cfg` / `sampler_name` / `scheduler` をサンプラーの同名 `inputs` から取得。リンクなら辿る。`seed` 不在時は `noise_seed`。
  - モデル名: `model` 入力を辿り `ckpt_name`、なければ `unet_name`。`model` 入力を持つ中継ノード（LoRA ローダー等）はさらに辿る。
  - 生成解像度: `latent_image` を辿り `width` / `height` を持つノードで採用。到達不能なら未取得（実ファイル解像度で代替しない）。
  - `status`: positive / negative / model_name / seed / steps / cfg / sampler_name / scheduler がすべて非 None なら `full`、それ以外は `partial`（`none` の判定は TASK-12 の呼び出し側）。プロンプト文字列は TASK-11 まで常に `None` のため本タスクでは `partial` のみ検証する。
  - 検証: `pytest tests/test_comfy_metadata.py`
    - 標準構成（CheckpointLoaderSimple → KSampler、EmptyLatentImage）→ 5 パラメータ / モデル名 / 幅高さが一致
    - KSamplerAdvanced（`noise_seed`）→ `seed` に採用
    - サンプラーなし → 全項目 `None`、`partial`
    - サンプラー 2 つ + SaveImage → SaveImage から辿れる側を採用
    - サンプラー 2 つ、SaveImage なし → ID 最小を採用
    - LoraLoader を挟む → `ckpt_name` に到達
    - UNETLoader → `unet_name` に到達
    - `latent_image` が width/height を持たないノード → 幅高さ `None`
    - 循環リンク → 例外なく終了し当該項目が `None`
    - `steps` がプリミティブノード経由のリンク → 解決される
- 関連: FR-8, FR-10, FR-11

### TASK-11

- ID: TASK-11
- 目的: positive / negative プロンプト文字列の抽出（リンク経由・条件付け加工ノードの連結・原文保持）を実装する
- 対象ファイル:
  - 変更: `app/comfy_metadata.py`
  - 追加: `tests/fixtures/prompts/*.json`
  - 変更: `tests/test_comfy_metadata.py`
- 完了条件:
  - サンプラーの `positive` / `negative` を辿り、到達ノードの `inputs.text` が文字列なら採用。`text` がリンクならさらに辿る（プリミティブノード経由）。
  - 到達ノードが `inputs` に conditioning リンクを持つ加工ノード（`ConditioningCombine`、`ConditioningConcat`、`ControlNetApply` 等）の場合、その conditioning リンクを再帰的に辿り、見つかった文字列を出現順に改行 2 つ（`\n\n`）で連結する。
  - 抽出した文字列に一切の加工（前後空白除去、改行正規化、エスケープ処理）を行わない。
  - 8 項目がすべて取得できたとき `status='full'` になる。
  - 検証: `pytest tests/test_comfy_metadata.py`
    - 標準構成で positive / negative が入力文字列と `==` で一致。入力には先頭・末尾空白、`\r\n` を含む改行、`\(...\)` のバックスラッシュ、全角文字を含める
    - `text` がプリミティブノードへのリンク → 解決される
    - `ConditioningCombine` で 2 つの `CLIPTextEncode` を結合 → `"A\n\nB"` の順序で連結
    - 標準構成 → `status='full'`、negative の `CLIPTextEncode` を欠く構成 → `partial`
- 関連: FR-8, FR-9, FR-10, FR-11 / AC-6（解析部分）, AC-7（解析部分）

### TASK-12

- ID: TASK-12
- 目的: スキャナにメタデータ抽出を統合し、生 JSON と抽出結果を保存して詳細 API で返す
- 対象ファイル:
  - 変更: `app/scanner.py`
  - 変更: `app/repository.py`（`insert_raw_metadata`, `insert_image` の抽出列対応）
  - 変更: `app/api.py`（`GET /images/{id}` の `generation` / `extractionStatus` を実データに）
  - 変更: `tests/conftest.py`（`prompt` / `workflow` を tEXt に埋め込む PNG 生成ヘルパ）
  - 新規: `tests/test_scan_metadata.py`
- 完了条件:
  - 新規登録時に Pillow の `img.info` から `prompt` / `workflow` を取得する。
  - どちらも存在しない → `extraction_status='none'`。`image_raw_metadata` 行は作らない。ファイル情報のみで登録し、エラーとして扱わない。
  - いずれか存在 → `image_raw_metadata` に原文のまま保存する。`prompt` を JSON としてパースし TASK-10/11 の `extract` を実行、結果を `images` の抽出列に保存し `extraction_status` を `full` / `partial` にする。
  - `prompt` が不在、または JSON パース不能な場合の `extraction_status` → 確認事項 #16。
  - 解析中の例外はスキャンを中断せず、当該画像を登録して次へ進む。
  - `scan_runs.extract_failed_count` を記録する（定義 → 確認事項 #3）。
  - 再スキャンのパス更新（TASK-7）で抽出列と生 JSON が変化しない。
  - `GET /images/{id}` の `generation` 各項目と `extractionStatus`、`GET /images` の `extractionStatus` が実データになる。
  - 検証: `pytest tests/test_scan_metadata.py`
    - 標準ワークフロー相当の `prompt` を埋め込んだ PNG をスキャン → `GET /images/{id}` の `positivePrompt` / `negativePrompt` が埋め込んだ文字列と完全一致、`modelName` / `seed` / `steps` / `cfg` / `samplerName` / `scheduler` が一致、`extractionStatus='full'`（AC-6）
    - KSampler を含まない `prompt` / カスタムノードのみの `prompt` を埋め込んだ PNG → スキャンが完了し `extractionStatus='partial'`、取得できた項目のみ非 `null`（AC-7）
    - メタデータなし PNG → `extractionStatus='none'`、`fileName` / `fileSize` / `imageWidth` / `imageHeight` / `fileMtime` が取得できる（AC-9）
    - `image_raw_metadata.prompt_json` / `workflow_json` が埋め込んだ文字列と `==` で一致（原文保持）
    - `POST /scan` の `extractFailedCount` が確認事項 #3 の定義に従った値になる
- 関連: FR-7, FR-8, FR-10, FR-11, FR-12, FR-13, FR-17 / AC-6, AC-7, AC-9

### TASK-13

- ID: TASK-13
- 目的: `GET /images/{id}/raw-metadata` を実装する
- 対象ファイル:
  - 変更: `app/api.py`
  - 変更: `app/repository.py`（`get_raw_metadata`）
  - 新規: `tests/test_api_raw_metadata.py`
- 完了条件:
  - `image_raw_metadata` の `prompt_json` / `workflow_json` をパース済み JSON として返す（トップレベルのキー名 → 確認事項 #13）。
  - 画像未存在、または `image_raw_metadata` 行なし → `404 NOT_FOUND`。
  - パース不能な生 JSON の扱い → 確認事項 #13。
  - 検証: `pytest tests/test_api_raw_metadata.py`
    - `extractionStatus='partial'` の画像で 200、内容が埋め込んだ JSON を `json.loads` した結果と一致する（AC-8）
    - `extractionStatus='none'` の画像 → 404 `NOT_FOUND`
    - 未存在 ID → 404 `NOT_FOUND`
- 関連: FR-7, FR-10 / AC-8

---

## フェーズ 4: サムネイル

### TASK-14

- ID: TASK-14
- 目的: WebP サムネイル生成関数を実装する
- 対象ファイル:
  - 新規: `app/thumbnailer.py`（`generate_thumbnail(src_path, out_dir, max_edge, quality) -> str`）
  - 新規: `tests/test_thumbnailer.py`
- 完了条件:
  - ファイル名は `YYYYMMDD_HHMMSS_<uuid4 先頭 8 桁>.webp`（日時は生成時のローカルタイム）。戻り値はファイル名のみ。
  - `out_dir` 直下にフラットに出力する（サブディレクトリを作らない）。
  - `Image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)` でアスペクト比維持・長辺 `max_edge` 以下に縮小。元画像の長辺が `max_edge` 以下なら元寸法のまま（拡大しない）。
  - `save(format="WEBP", quality=quality, method=4)`。アルファチャンネルは保持する。
  - 元画像を変更しない。破損画像では例外を送出し、呼び出し側（TASK-15）で処理する。
  - 検証: `pytest tests/test_thumbnailer.py`
    - 2048×1024 → 出力の長辺が `max_edge` と等しく、比率が 2:1（AC-17。長辺の期待値 512 と 768 の食い違い → 確認事項 #1）
    - 300×200 → 出力が 300×200（AC-18）
    - RGBA 入力 → 出力 WebP にアルファがある
    - ファイル名が `^\d{8}_\d{6}_[0-9a-f]{8}\.webp$` に一致し、2 回生成で名前が異なる
    - 生成前後で元ファイルの SHA-256 が不変
    - 不正なバイト列のファイル → 例外
- 関連: FR-25, FR-26 / AC-17, AC-18

### TASK-15

- ID: TASK-15
- 目的: スキャナにサムネイル生成を統合する（新規時生成、失敗記録、失敗時のみ再試行、件数集計）
- 対象ファイル:
  - 変更: `app/scanner.py`
  - 変更: `app/repository.py`（`set_thumbnail`）
  - 新規: `tests/test_scan_thumbnail.py`
- 完了条件:
  - 新規登録時にサムネイルを生成し `thumbnail_name` / `thumbnail_status='ok'` を記録する。
  - 生成失敗 → `thumbnail_status='failed'` を記録し、スキャンを中断せず次のファイルへ進む。
  - Pillow で開けない・読み込めない破損 PNG もレコードを登録し `thumbnail_status='failed'` とする（他の列の扱い → 確認事項 #7）。
  - 登録済みかつ `file_path` 一致で `thumbnail_status='failed'` → サムネイル生成のみ再試行する。`ok` のものは再生成しない。パス不一致時の再試行 → 確認事項 #5。
  - `scan_runs.thumbnail_generated_count` / `thumbnail_failed_count` を記録する。
  - 元画像の削除・変更を行わない。missing になった画像のサムネイルファイルを削除しない。孤児サムネイルも削除しない。
  - 検証: `pytest tests/test_scan_thumbnail.py`
    - スキャン後、新規登録の各画像に `.thumbnails/<thumbnail_name>` が存在し WebP として開ける（AC-17 の存在確認）
    - スキャン前後で `scan_root` 配下の PNG のパス集合と各 SHA-256 が同一、増えたファイルが `.thumbnails/` 配下のみ。再スキャンしても `.thumbnails/` 内のファイルが `images` に登録されない（AC-19）
    - 破損 PNG（ランダムバイト列 + 途中で切れた PNG の 2 種）を混在 → `POST /scan` が 200 で完了、当該レコードが `thumbnail_status='failed'`、他の画像は `ok`（AC-20）
    - 同じフォルダを続けて 2 回スキャン → 2 回目の `thumbnailGeneratedCount` が 0（AC-21）
    - `generate_thumbnail` を 1 回目失敗・2 回目成功のスタブに差し替え → 1 回目 `failed`、2 回目 `ok`、`thumbnail_name` が設定される（FR-29 の再試行）
    - ファイル削除 → 再スキャン後も `.thumbnails/<thumbnail_name>` が残っている（FR-31）
- 関連: FR-25, FR-27, FR-28, FR-29, FR-32, NFR-2, NFR-3 / AC-19, AC-20, AC-21

### TASK-16

- ID: TASK-16
- 目的: `GET /images/{id}/thumbnail` と `GET /images/{id}/file` を実装する
- 対象ファイル:
  - 変更: `app/api.py`
  - 新規: `tests/test_api_binary.py`
- 完了条件:
  - `GET /images/{id}/thumbnail`: `scan_root/<thumbnail_dir_name>/<thumbnail_name>` を `image/webp` で返し、`Cache-Control: public, max-age=86400` を付与する。`presence='missing'` でもファイルがあれば返す。`thumbnail_status != 'ok'` またはファイル不在 → `404 THUMBNAIL_UNAVAILABLE`。
  - `GET /images/{id}/file`: `scan_root/<file_path>` を `image/png` で返す。`presence='missing'` またはファイル不在 → `404 FILE_MISSING`。
  - いずれも未存在 ID → `404 NOT_FOUND`。
  - 検証: `pytest tests/test_api_binary.py`
    - 元画像を削除して再スキャン後、`GET /images?include_missing=true` に当該画像が含まれ、`GET /images/{id}/thumbnail` が 200 / `image/webp` で返る（AC-22）
    - `GET /images/{id}/file` の本文が元ファイルのバイト列と一致し、`Content-Type` が `image/png`
    - missing 画像の `/file` → 404 `FILE_MISSING`、`thumbnail_status='failed'` の `/thumbnail` → 404 `THUMBNAIL_UNAVAILABLE`
    - `/thumbnail` の応答ヘッダに `Cache-Control: public, max-age=86400`
- 関連: FR-18, FR-30, FR-31 / AC-22

---

## フェーズ 5: フォルダ

### TASK-17

- ID: TASK-17
- 目的: `GET /folders` を実装し、`dir_path` からフォルダツリーと件数を導出する
- 対象ファイル:
  - 変更: `app/api.py`
  - 変更: `app/repository.py`（`count_by_dir_path`）
  - 新規: `app/folders.py` または `app/api.py` 内のツリー組み立て関数（design のファイル構成に追加ファイルを設けるかは実装者判断。ロジックは純粋関数として単体テスト可能にする）
  - 新規: `tests/test_api_folders.py`
- 完了条件:
  - `SELECT dir_path, COUNT(*) FROM images WHERE presence='active' GROUP BY dir_path` から、`/` 分割で入れ子構造を組み立てる。
  - 中間ディレクトリ（画像を直接持たない階層）もノードとして生成し `directCount=0`。`totalCount` は自身と全子孫の `directCount` の合計。
  - `<thumbnail_dir_name>` および `.` で始まるセグメントを含むパスは除外する。
  - `rootTotalCount` は active 画像の総件数。応答は design §7 の形式（`name`, `path`, `directCount`, `totalCount`, `children`）。
  - 兄弟ノードの並び順 → 確認事項 #17。
  - 検証: `pytest tests/test_api_folders.py`
    - `a/`（2 件）, `a/b/`（3 件）, `c/`（1 件）, ルート直下（1 件）をスキャン → `a.directCount=2`, `a.totalCount=5`, `a.children[0].path='a/b'`, `c.totalCount=1`, `rootTotalCount=7`（AC-23）
    - `x/y/` のみに画像 → `x` が `directCount=0`, `totalCount=y の件数` で生成される
    - missing 画像は件数に含まれない
    - `repository` で `dir_path='.thumbnails'` / `.hidden/z` の行を直接挿入しても応答に現れない（AC-24）
- 関連: FR-33, FR-34, FR-35 / AC-23, AC-24

### TASK-18

- ID: TASK-18
- 目的: `GET /images` に `dir` / `recursive` によるフォルダ絞り込みを追加する
- 対象ファイル:
  - 変更: `app/api.py`
  - 変更: `app/repository.py`
  - 変更: `tests/test_api_images.py`
- 完了条件:
  - query `dir`（省略可）, `recursive`（既定 true）。
  - `recursive=false` → `dir_path = :dir`。`true` → `dir_path = :dir OR dir_path LIKE :dir || '/%'`。
  - `dir` が空文字列・未指定の場合の挙動 → 確認事項 #8。
  - `favorite_only` / `include_missing` / カーソルと併用でき、`totalCount` は同一条件の件数。
  - 検証: `pytest tests/test_api_images.py`
    - `a/`, `a/b/`, `ab/` に画像を配置し、`dir=a&recursive=false` → `a/` 直下のみ、`dir=a&recursive=true` → `a/` と `a/b/` の画像のみで `ab/` を含まない（AC-25）
    - `dir=a&recursive=true&favorite_only=true` で両条件の AND になる
    - `totalCount` が絞り込み後の件数と一致する
- 関連: FR-36, FR-38 / AC-25

---

## フェーズ 6: フロントエンド

### TASK-19

- ID: TASK-19
- 目的: 静的ファイル配信と、中央ペインのサムネイルグリッド・逐次取得を実装する
- 対象ファイル:
  - 変更: `app/api.py`（`StaticFiles` のマウント。API ルート登録後にマウントする）
  - 新規: `app/static/index.html`（3 ペインのレイアウトと CSS）
  - 新規: `app/static/app.js`
- 完了条件:
  - ビルド工程なし。`index.html` + `app.js` のみで動作する。配信パス → 確認事項 #14。
  - 中央ペインは CSS Grid `grid-template-columns: repeat(auto-fill, minmax(<cell>px, 1fr))`。各セルは `<img src="{thumbnailUrl}" alt="{fileName}">` でサムネイルのみを参照し、原寸画像を取得しない。
  - 初回は `GET /images` を取得し、末尾の番兵要素が可視になった時点（`IntersectionObserver`）で `nextCursor` を付けて続きを取得する。`nextCursor` が `null` なら以後取得しない。二重取得を防ぐ。
  - 絞り込み条件を変更する API を `app.js` に用意し、呼ばれたらカーソルと取得済み項目を破棄して先頭から取り直す（左ペインからの利用は TASK-21）。
  - 検証（手動）:
    - `python -m app` 起動後、ブラウザで開くとスキャン済み画像のサムネイルがグリッド表示される
    - 末尾までスクロールすると続きが読み込まれ、最終的に表示件数が初回応答の `totalCount` と一致する（AC-26 の UI 側確認）
    - ブラウザの Network タブで `/images/*/file` への要求が発生しない（NFR-4）
- 関連: FR-14, FR-37, NFR-4 / AC-26（UI 側確認）

### TASK-20

- ID: TASK-20
- 目的: サムネイル表示サイズのスライダーを実装し、最大サイズでも画像を引き伸ばさない
- 対象ファイル:
  - 変更: `app/static/index.html`, `app/static/app.js`
- 完了条件:
  - スライダーで `<cell>` を `grid_min_cell` 〜 `grid_max_cell` の範囲で変更できる（フロントエンドが設定値を得る手段 → 確認事項 #11）。
  - `<img>` は `max-width: 100%; width: auto; height: auto` 等により、セル幅がサムネイルの実解像度を超えても元解像度を超えて拡大表示しない。
  - 検証（手動）:
    - スライダーを最大にし、長辺 768px のサムネイルと、元画像が小さいため 768px 未満のサムネイル（例: 200px）の両方について、DevTools で `<img>` の描画幅が `naturalWidth` 以下であることを確認する（AC-27）
    - スライダー操作でグリッドの列数が変わる
- 関連: FR-39 / AC-27

### TASK-21

- ID: TASK-21
- 目的: 左ペイン（固定項目とフォルダツリー）を実装し、絞り込みを切り替えられるようにする
- 対象ファイル:
  - 変更: `app/static/index.html`, `app/static/app.js`
- 完了条件:
  - 固定項目「すべて」「お気に入り」「見つからない」を表示する。「すべて」は絞り込みなし、「お気に入り」は `favorite_only=true`。「見つからない」の絞り込み条件と、固定項目の件数の取得元 → 確認事項 #10。
  - `GET /folders` からフォルダツリーを構築し、各ノードに件数を表示する（`directCount` / `totalCount` のどちらを表示するか → 確認事項 #10）。
  - フォルダ選択で `dir` を指定して一覧を取り直す。`recursive` の指定手段 → 確認事項 #10。スキャン実行ボタンの有無 → 確認事項 #12。
  - 項目の選択で TASK-19 の絞り込み変更 API を呼び、カーソルと取得済み項目を破棄して先頭から取り直す。
  - 検証（手動）:
    - サブフォルダを含む `scan_root` をスキャンした状態で、左ペインにツリーが入れ子で表示され件数が `GET /folders` の応答と一致する
    - フォルダをクリックすると中央ペインがそのフォルダの画像に切り替わり、末尾まで逐次取得できる
    - 「お気に入り」をクリックするとお気に入り画像のみが表示される
- 関連: FR-15, FR-16, FR-33, FR-34, FR-36

### TASK-22

- ID: TASK-22
- 目的: 右ペイン（選択中画像の詳細・プロンプトのコピー・抽出状態の表示）を実装する
- 対象ファイル:
  - 変更: `app/static/index.html`, `app/static/app.js`
- 完了条件:
  - サムネイルのクリックで `GET /images/{id}` を取得し、ファイル情報（ファイル名、パス、サイズ、解像度、更新日時）、生成パラメータ（モデル名、seed、steps、cfg、sampler、scheduler、生成解像度）、positive / negative プロンプト、抽出状態、お気に入り状態を表示する。お気に入り切り替え UI の有無 → 確認事項 #12。
  - 原寸画像は詳細表示時のみ `fileUrl` から取得して表示する（design §1）。
  - positive / negative それぞれに独立したコピーボタンを置き、API が返した文字列そのものを `navigator.clipboard.writeText` に渡す。表示上の整形（`white-space` 等）をコピー内容に反映しない。
  - `extractionStatus` が `partial` / `none` の場合、取得できなかった項目がある旨を表示し、`GET /images/{id}/raw-metadata` へのリンクを置く。
  - 検証（手動）:
    - 先頭・末尾に空白と複数行を含むプロンプトを持つ画像を選択してコピーし、エディタに貼り付けた内容が `GET /images/{id}` の応答文字列と完全一致する（AC-6 の UI 側確認、FR-9）
    - `partial` の画像で注意表示と生メタデータリンクが出て、リンク先で JSON が表示される
    - `none` の画像で注意表示が出る
    - missing 画像を選択した場合に詳細情報は表示され、原寸画像の 404 でページが壊れない
- 関連: FR-9, FR-17, FR-18 / AC-6（UI 側確認）, AC-8（UI 側確認）

---

## AC 対応表

| AC | 内容（要約） | 完了条件を持つタスク |
|---|---|---|
| AC-1 | サブフォルダ含め登録 | TASK-3 |
| AC-2 | 2 回スキャンで件数不変 | TASK-3 |
| AC-3 | 移動後の再スキャンでパス更新・お気に入り維持 | TASK-7 |
| AC-4 | 削除後の再スキャンで missing・お気に入り維持 | TASK-7 |
| AC-5 | スキャン結果の件数返却 | TASK-5（形式）, TASK-7（値） |
| AC-6 | 標準ワークフローの完全一致抽出 | TASK-11（解析）, TASK-12（API）, TASK-22（UI） |
| AC-7 | KSampler なし / カスタム構成で partial | TASK-11（解析）, TASK-12（API） |
| AC-8 | 抽出失敗でも生メタデータ参照可 | TASK-13 |
| AC-9 | メタデータなし PNG の登録とファイル情報 | TASK-3（ファイル情報）, TASK-12（none 判定） |
| AC-10 | 既定で更新日時降順 | TASK-6 |
| AC-11 | お気に入りオン → 絞り込みに含まれる | TASK-9 |
| AC-12 | お気に入りオフ → 絞り込みから外れる | TASK-9 |
| AC-13 | お気に入り操作でファイル不変 | TASK-8 |
| AC-14 | missing の既定除外と指定時包含 | TASK-9 |
| AC-15 | 未存在 ID で 404 / application/json | TASK-4（基盤）, TASK-6 |
| AC-16 | 不在フォルダのスキャンでエラー・データ不変 | TASK-5 |
| AC-17 | WebP サムネイル存在・長辺上限・アスペクト比 | TASK-14（寸法）, TASK-15（存在）※確認事項 #1 |
| AC-18 | 小さい画像を拡大しない | TASK-14 ※確認事項 #1 |
| AC-19 | 元ファイル不変・増加は専用ディレクトリのみ・再スキャンで登録されない | TASK-3（除外）, TASK-15 |
| AC-20 | 破損 PNG があっても完了し失敗記録 | TASK-15 |
| AC-21 | 2 回目のサムネイル生成件数 0 | TASK-15 |
| AC-22 | missing でもサムネイル取得可 | TASK-16 |
| AC-23 | フォルダ階層と直下 / 合計件数 | TASK-17 |
| AC-24 | 専用ディレクトリが階層に含まれない | TASK-17 |
| AC-25 | サブフォルダ含む / 含まない絞り込み | TASK-18 |
| AC-26 | 逐次取得で重複なし・総件数一致 | TASK-6, TASK-19（UI） |
| AC-27 | 最大表示でも実解像度を超えない | TASK-20 |

---

## 確認事項

実装方針を決めずに保留した箇所。回答後、該当タスクの完了条件を更新する。

1. **サムネイル長辺の値が矛盾している。** FR-26 と design §1 / §5 / §8 は既定 768px、AC-17 は「長辺が 512px 以下」、AC-18 は「長辺 512px 未満の画像」を基準にしている。既定値 768 で AC-17 を検証すると 1024px の画像は 768px に縮小され「512px 以下」を満たさない。どちらが正か（AC の記述を 768 に改めるか、既定値を 512 にするか）。→ TASK-14, TASK-15
2. design.md 冒頭の前提が「FR-1 〜 FR-32 / AC-1 〜 AC-22」だが、requirements.md は FR-39 / AC-27 まである（design.md 本文は FR-33〜39 / AC-23〜27 を扱っている）。前提行の更新漏れとみなしてよいか。
3. **`extract_failed_count`（抽出失敗件数）の定義が未記載。** (a) `extraction_status='partial'` の件数、(b) `partial` + `none` の件数、(c) 解析中に例外が出た件数、のいずれか。また design §3 末尾「個々のファイルの処理失敗（読み込み不可、破損 PNG 等）…失敗件数としてカウント」がどのカウンタを指すか。→ TASK-12
4. **`updated_count` / `missing_count` の定義が未記載。** `updated_count` はパス不一致による更新のみか、`missing → active` の復帰も含むか。`missing_count` は今回のスキャンで新たに missing になった件数か、スキャン終了時点の missing 総数か。→ TASK-7
5. **パス不一致時の更新列。** design §3 手順 5 は `file_path` / `file_name` / `file_mtime` / `presence` を更新すると記載し `dir_path` を含まない。`dir_path` を更新しないとフォルダ絞り込み（FR-36）とフォルダツリー（FR-33）が旧位置を指す。`dir_path` も更新する認識でよいか。また同ケースで `thumbnail_status='failed'` の再試行（FR-29）を行うか（design は `file_path` 一致の場合のみ再試行と記載）。→ TASK-7, TASK-15
6. **同一スキャン内で同一ハッシュのファイルが複数パスに存在する場合の扱い。** 手順 5 に従うと後から処理したパスで上書きされ、走査順に依存する。どちらのパスを採用するか、または未定義でよいか。→ TASK-3
7. **破損 PNG（Pillow で開けない / 読み込めない）の登録内容。** AC-20 は「当該画像は生成失敗として記録され」とありレコードが存在する前提だが、`image_width` / `image_height` を NULL にするか、`extraction_status` を何にするか（`none` か `partial` か）、`scanned_count` / `created_count` に含めるかが未記載。→ TASK-3, TASK-15
8. **`GET /images` の `dir` 空文字列の扱いが design §6 内で矛盾している。** query の説明では「空文字列はルート直下」、flow では「`dir` が空文字列の場合は条件を付けない」。`recursive=false` かつ `dir=''` でルート直下のみ（`dir_path = ''`）を返すべきか、全件を返すべきか。また `dir` 未指定と `dir=''` を区別するか。→ TASK-18
9. **`VALIDATION_ERROR` の HTTP ステータスコードが未記載**（400 か 422 か）。あわせて `limit` が 300 を超えた場合（300 に丸めるかエラーか）、`limit <= 0`、復号不能な `cursor` の扱い。→ TASK-4, TASK-6
10. **左ペインの固定項目と API の不整合。** (a) 「見つからない」に対応する API 絞り込みがない（`include_missing=true` は active も含む。missing のみを返す条件が存在しない）。(b) 固定項目の件数（お気に入り件数、missing 件数）を返す API がない（`GET /images` の `totalCount` を件数取得目的で呼ぶことを許容するか）。(c) フォルダノードに表示する件数が `directCount` / `totalCount` のどちらか。(d) `recursive` を UI でどう指定するか（トグルの有無）。→ TASK-21
11. **`grid_min_cell` / `grid_max_cell` をフロントエンドに渡す手段がない。** 設定値は `config.toml` にあるが、設定を返すエンドポイントが design §6 にない。設定取得 API を追加するか、`index.html` に既定値を埋め込むか。→ TASK-20
12. **フロントエンドの操作 UI が §9 に未記載。** FR-2「スキャンは利用者の明示的な操作で実行」に対応するスキャン実行ボタンと、FR-19 / FR-20 に対応するお気に入り切り替え UI が design §9 のレイアウトに含まれていない。API のみで満たすとみなすか、UI に含めるか。含める場合はタスクを追加する。→ TASK-21, TASK-22
13. **`GET /images/{id}/raw-metadata` のレスポンス形状が未記載**（例: `{"prompt": {...}, "workflow": {...}}`。片方のみ存在する場合は `null` か）。また `prompt_json` / `workflow_json` が JSON としてパース不能な場合に何を返すか。→ TASK-13
14. **静的ファイルの配信パスが未記載。** `/` で `index.html` を返すか、`/static/` 配下で配信するか。→ TASK-19
15. **`db_path` 既定 `./data/images.db` の基準ディレクトリ**（カレントディレクトリか、`config.toml` のあるアプリケーションルートか）。`data/` ディレクトリを自動作成してよいか。→ TASK-1, TASK-2
16. **`prompt` チャンクが不在で `workflow` のみ存在する場合、および `prompt` が JSON としてパース不能な場合の `extraction_status`。** design §4 の定義（`none` はいずれも存在しない場合のみ）からは `partial` と読めるが、明示されていない。→ TASK-12
17. **`GET /folders` の兄弟ノードの並び順が未記載**（名前昇順か、件数順か）。→ TASK-17
18. **`file_mtime` の精度**（秒か、サブ秒を含めるか）。カーソル比較と AC-10 の同一秒内の順序に影響する。design §7 の例は秒精度。→ TASK-3, TASK-6

---

## 採用した解釈（確認事項への暫定回答）

利用者の指示「回答がない項目は design.md から最も素直に読める解釈を採用し、採用内容を明記して進める」に基づく暫定決定。正式な回答があれば差し替える。各項目はコード側にも `# 確認事項 #n` のコメントで印を付ける。

| # | 採用した解釈 |
|---|---|
| 1 | 既定値 768px（FR-26 / design §1）を採用する。AC-17 / AC-18 の 512 は旧値とみなし、テストは `thumbnail_max_edge` の設定値を基準に検証する。 |
| 2 | design.md 冒頭の前提行は更新漏れとみなし、FR-39 / AC-27 まで対象とする。 |
| 3 | `extract_failed_count` = 今回のスキャンで新規登録した画像のうち `extraction_status='partial'` の件数（メタデータはあるが全項目を取得できなかったもの）。`none` は失敗に含めない。Pillow で開けないファイルはサムネイル失敗（`thumbnail_failed_count`）として数える。 |
| 4 | `updated_count` = パス不一致で更新したレコード数（missing → active の復帰は含めない）。`missing_count` = 今回のスキャンで新たに `missing` になったレコード数。 |
| 5 | パス不一致時は `dir_path` も更新する。`thumbnail_status='failed'` の再試行はパス一致・不一致どちらでも行う。 |
| 6 | 同一スキャン内で同一ハッシュが複数パスに現れた場合、走査順（パスのソート順）で最初のものを採用し、以降は走査件数にのみ数えて無視する。 |
| 7 | 破損 PNG もレコードを登録する。`image_width` / `image_height` は NULL、`extraction_status='none'`、`thumbnail_status='failed'`。`scanned_count` / `created_count` に含める。ハッシュ算出自体ができない（読み取り不可）ファイルは走査件数に数えて登録しない。 |
| 8 | `dir` 未指定 → フォルダ条件なし。`dir=''` かつ `recursive=false` → `dir_path = ''`（ルート直下のみ）。`dir=''` かつ `recursive=true` → 条件なし（ルート配下すべて）。 |
| 9 | `VALIDATION_ERROR` は 422。`limit` は 1〜300 の範囲外、復号不能な `cursor` はいずれも 422 `VALIDATION_ERROR`。 |
| 10 | (a) `GET /images` に `missing_only`（既定 false、`presence='missing'` のみ）を追加する。(b) `GET /folders` の応答に `favoriteCount`（active かつお気に入り）と `missingCount` を追加する。(c) フォルダノードには `totalCount` を表示する。(d) 左ペインに「サブフォルダを含む」チェックボックス（既定 on）を置く。 |
| 11 | `GET /config` を追加し `{"gridMinCell", "gridMaxCell", "thumbnailMaxEdge"}` を返す。 |
| 12 | 左ペイン上部にスキャン実行ボタン（`POST /scan` を呼び結果件数を表示）、右ペインにお気に入り切り替えボタンを置く。 |
| 13 | 応答は `{"prompt": ..., "workflow": ...}`。存在しない側は `null`。JSON としてパースできない場合はその原文文字列をそのまま値にする。 |
| 14 | `StaticFiles(html=True)` を `/` にマウントし、`/` で `index.html`、`/app.js` で JS を返す。API ルートはマウントより先に登録する。 |
| 15 | `db_path` の相対パスは `config.toml` のあるディレクトリを基準に解決する。親ディレクトリは自動作成する。 |
| 16 | `prompt` / `workflow` の少なくとも一方が存在すれば `none` にはしない。`prompt` 不在または JSON パース不能なら全項目未取得の `partial`。生データは保存する。 |
| 17 | 兄弟ノードは `name` の昇順。 |
| 18 | `file_mtime` は秒精度の UTC（`YYYY-MM-DDTHH:MM:SSZ`）。同一秒内は `id` 降順で順序が決まる。 |

---

## 実装状況

TASK-1 〜 TASK-22 をすべて実装済み（各タスク 1 コミット、コミット件名に `TASK-n:` を付与）。
自動テストは `python -m pytest`（111 件）で完了条件を検証している。フロントエンドの手動確認項目（TASK-19〜22）は Playwright + Chromium で以下を確認した。

- 100 件 → 266 件の逐次取得で表示件数が `totalCount` と一致し、`/images/{id}/file` への要求が一覧で発生しない（AC-26 / NFR-4）
- セル幅を最大にしても `<img>` の描画幅が `naturalWidth` を超えない（AC-27）
- フォルダ選択・サブフォルダ切替・お気に入り・見つからない・スキャンボタンが動作する
- Positive / Negative のコピー内容が API の返す文字列と完全一致する（FR-9）
- partial / none / missing の各表示と生メタデータリンク

「採用した解釈」の表に従って実装した箇所はコード内に `確認事項 #n` のコメントを付けているため、正式回答が出た際はそのコメントを検索して修正する。
