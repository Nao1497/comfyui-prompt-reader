# design.md

前提: [requirements.md](./requirements.md) の FR-1 〜 FR-32 / AC-1 〜 AC-22 を満たす。

---

## 1. Architecture

### 実行環境

- Docker は使用しない。ホスト上で直接起動する。
- 起動コマンドは単一とする（例: `python -m app`）。フロントエンドも同一プロセスから配信する。

### バックエンド

- Python 3.11 以上
- Web フレームワーク: FastAPI
- ASGI サーバ: uvicorn（アプリケーション内から起動する。外部プロセス管理を前提としない）
- 画像処理: Pillow（PNG の tEXt チャンク読み取り、WebP 書き出しの両方に使用）
- DB: SQLite（標準ライブラリ `sqlite3` を直接使用。ORM は導入しない）

### フロントエンド

- 単一の静的 HTML + JavaScript とし、ビルド工程を持たない。
- FastAPI の `StaticFiles` で配信する。
- 一覧はサムネイル URL を `<img>` で参照する。原寸画像は詳細表示時のみ取得する。

### 設定

- 設定ファイル `config.toml` をアプリケーションルートに置く。
- 設定項目:
  - `scan_root`: スキャン対象のルートフォルダ絶対パス
  - `thumbnail_dir_name`: サムネイル格納ディレクトリ名（既定 `.thumbnails`）
  - `thumbnail_max_edge`: サムネイル長辺の上限（既定 `768`）
  - `thumbnail_quality`: WebP 品質（既定 `80`）
  - `grid_min_cell` / `grid_max_cell`: 一覧セル幅の下限・上限（既定 `96` / `320`、単位 px）
  - `db_path`: SQLite ファイルパス（既定 `./data/images.db`）
  - `host` / `port`（既定 `127.0.0.1` / `8000`）
  - `rename_on_scan`: スキャン時の自動リネーム（FR-40）を行うか（既定 `true`）

### ディレクトリ構成

```
app/
├── __main__.py          # 起動エントリポイント
├── config.py            # config.toml 読み込み
├── db.py                # 接続、スキーマ初期化、マイグレーション
├── models.py            # dataclass 定義
├── repository.py        # SQL 実行の集約
├── scanner.py           # フォルダ走査、ハッシュ算出、登録・更新
├── comfy_metadata.py    # ComfyUI グラフ解析
├── thumbnailer.py       # WebP サムネイル生成
├── api.py               # FastAPI ルーティング
└── static/
    ├── index.html
    └── app.js
config.toml
data/
└── images.db
```

---

## 2. Data Model

SQLite 設定:

- `PRAGMA journal_mode = WAL`
- `PRAGMA foreign_keys = ON`
- 日時は UTC の ISO8601 文字列で保存する。

### `images`

一覧クエリで参照される列のみを持つ。巨大な生 JSON は別テーブルに分離する。

- 用途: 画像1件の基本情報・生成パラメータ・状態
- columns:
  - `id` (INTEGER, PK, AUTOINCREMENT)
  - `content_hash` (TEXT, NOT NULL, UNIQUE) — SHA-256 の16進文字列。同一性の基準
  - `file_path` (TEXT, NOT NULL) — `scan_root` からの相対パス
  - `dir_path` (TEXT, NOT NULL) — `scan_root` からの相対ディレクトリ。ルート直下は空文字列。区切りは `/` に正規化する
  - `file_name` (TEXT, NOT NULL)
  - `file_size` (INTEGER, NOT NULL)
  - `image_width` (INTEGER) — 実ファイルの幅
  - `image_height` (INTEGER)
  - `file_mtime` (TEXT, NOT NULL) — 一覧の既定ソートキー
  - `presence` (TEXT, NOT NULL) — `active` / `missing`
  - `is_favorite` (INTEGER, NOT NULL, default 0) — 0/1
  - `thumbnail_name` (TEXT) — サムネイルのファイル名のみ（ディレクトリは設定から解決）
  - `thumbnail_status` (TEXT, NOT NULL) — `ok` / `failed` / `pending`
  - `extraction_status` (TEXT, NOT NULL) — `full` / `partial` / `none`
  - `positive_prompt` (TEXT)
  - `negative_prompt` (TEXT)
  - `model_name` (TEXT)
  - `seed` (INTEGER)
  - `steps` (INTEGER)
  - `cfg` (REAL)
  - `sampler_name` (TEXT)
  - `scheduler` (TEXT)
  - `gen_width` (INTEGER) — ワークフロー上の指定解像度
  - `gen_height` (INTEGER)
  - `created_at` (TEXT, NOT NULL)
  - `updated_at` (TEXT, NOT NULL)
- indexes:
  - `uk_images_content_hash (content_hash)` unique
  - `idx_images_cursor (file_mtime DESC, id DESC)` — 逐次取得のカーソル用
  - `idx_images_favorite (is_favorite, file_mtime DESC, id DESC)`
  - `idx_images_dir (dir_path, file_mtime DESC, id DESC)` — フォルダ絞り込み用
  - `idx_images_presence (presence)`

### `image_raw_metadata`

- 用途: ComfyUI の生 JSON 保持（詳細取得時のみ読む）
- columns:
  - `image_id` (INTEGER, PK) — `images.id` を参照
  - `prompt_json` (TEXT) — tEXt チャンク `prompt` の原文
  - `workflow_json` (TEXT) — tEXt チャンク `workflow` の原文

### `scan_runs`

- 用途: スキャン実行の結果記録
- columns:
  - `id` (INTEGER, PK, AUTOINCREMENT)
  - `started_at` (TEXT, NOT NULL)
  - `finished_at` (TEXT)
  - `scanned_count` / `created_count` / `updated_count` / `missing_count` / `extract_failed_count` / `thumbnail_generated_count` / `thumbnail_failed_count` / `renamed_count` (INTEGER, NOT NULL, default 0)
  - `error` (TEXT)

### リレーション方針

- `image_raw_metadata.image_id` に `images.id` への外部キーを設定し、`ON DELETE CASCADE` とする。
- 論理削除は行わない。存在しない画像は `presence = 'missing'` で表現し、レコードは残す（FR-5）。

---

## 3. Scan Design

`POST /scan` の処理順序。同時実行は許可せず、実行中の再要求は `409` を返す。

1. `scan_root` の存在を確認。存在しなければ何も変更せず `400 INVALID_SCAN_ROOT` を返す（FR-24 / AC-16）。
2. `scan_root/<thumbnail_dir_name>` を作成（存在すれば何もしない）。
3. `scan_root` 配下を再帰走査し、拡張子 `.png`（大文字小文字を問わない）のファイルを列挙する。
   - `<thumbnail_dir_name>` 配下は走査対象から除外する（FR-32）。
   - 名前が `.` で始まるディレクトリは除外する。
4. `rename_on_scan` が有効で、ファイル名が `^\d{8}T\d{6}_[0-9a-f]{8}\.png$` に合わない場合、同一ディレクトリ内で `<mtime のローカルタイム YYYYMMDDTHHMMSS>_<uuid4 先頭8桁>.png` にリネームする（FR-40）。衝突時は uuid を取り直す。`os.rename` は mtime を変更しないため更新日時は保たれる。失敗時は警告を記録し元の名前で続行する。以降の手順はリネーム後のパスで行う。
   - 各ファイルについて SHA-256 を算出する（64KB 単位のストリーム読み込み）。
5. `content_hash` で既存レコードを検索する。
   - 未登録 → 新規登録処理へ（6 以降）
   - 登録済みかつ `file_path` が一致 → `presence = 'active'` に更新するのみ。`thumbnail_status = 'failed'` の場合はサムネイル生成のみ再試行する（FR-29）
   - 登録済みかつ `file_path` が不一致 → `file_path` / `file_name` / `file_mtime` / `presence` を更新する。`is_favorite`、抽出済みメタデータ、`thumbnail_name` は変更しない（FR-4 / AC-3）
6. Pillow で画像を開き、`image_width` / `image_height` / `file_size` / `file_mtime` を取得する（FR-13）。`file_path` と `dir_path` は `scan_root` からの相対パスとして記録し、区切り文字を `/` に正規化する。
7. メタデータ抽出（§4）を実行する。失敗しても中断せず `extraction_status` を設定する（FR-10）。
8. サムネイル生成（§5）を実行する。失敗しても中断せず `thumbnail_status = 'failed'` を記録し、次のファイルへ進む（FR-28 / AC-20）。
9. 全ファイル処理後、今回の走査で出現しなかった `presence = 'active'` のレコードを `missing` に更新する（FR-5）。レコードおよび `is_favorite` は削除・変更しない。
10. 集計値を `scan_runs` に記録し、レスポンスとして返す（FR-6）。

個々のファイルの処理失敗（読み込み不可、破損 PNG 等）はスキャン全体を停止させない。失敗件数としてカウントし、可能な範囲でレコードを登録する。

---

## 4. ComfyUI Metadata Extraction

### 入力

- Pillow で PNG を開いた際の `img.info` から `prompt` および `workflow` を取得する。
- 解析には `prompt`（API 形式）を使用する。`workflow` は生データとして保存するのみで解析に使用しない。
- どちらのキーも存在しない場合、`extraction_status = 'none'` とし、ファイル情報のみで登録する（FR-12 / AC-9）。

### `prompt` の構造

ノード ID をキーとする辞書である。

```json
{
  "3": {
    "class_type": "KSampler",
    "inputs": {
      "seed": 123456,
      "steps": 20,
      "cfg": 7.0,
      "sampler_name": "euler",
      "scheduler": "normal",
      "denoise": 1.0,
      "model": ["4", 0],
      "positive": ["6", 0],
      "negative": ["7", 0],
      "latent_image": ["5", 0]
    }
  }
}
```

- `inputs` の値が `[ノードID, 出力インデックス]` の2要素配列であればリンク、それ以外はリテラル値である。
- 解析はこのリンクを逆向きに辿ることで行う。

### 解決アルゴリズム

共通ヘルパ `resolve(value)`: 値がリンクであれば参照先ノードを返し、リテラルであればその値を返す。探索は深さ上限 32 とし、訪問済みノード集合で循環を防ぐ。いずれかに達した場合はその項目を未取得とする。

**1. サンプラーノードの特定**

- `inputs` に `positive` と `negative` の両方を持つノードを候補とする。`class_type` 名では判定しない（カスタムサンプラーを取りこぼすため）。
- 候補が0件 → プロンプト・生成パラメータをすべて未取得とし、`extraction_status = 'partial'`
- 候補が1件 → それを採用
- 候補が複数 → `SaveImage` / `PreviewImage` / `class_type` に `SaveImage` を含むノードから `images` 入力を逆向きに辿り、最初に到達したサンプラーを採用する。到達できない場合はノード ID の数値昇順で最小のものを採用する。

**2. プロンプト文字列**

- サンプラーの `positive` / `negative` をそれぞれ辿る。
- 到達したノードの `inputs` に `text` があり、その値が文字列であれば採用する。`text` がリンクの場合はさらに辿る（プリミティブノード経由に対応）。
- 到達したノードが条件付け加工ノード（`ConditioningCombine`、`ConditioningConcat`、`ControlNetApply` 等、`inputs` に conditioning リンクを持つノード）の場合、その conditioning リンクを再帰的に辿り、見つかった文字列を出現順に改行2つで連結する。
- 抽出した文字列は一切加工しない。前後の空白除去、改行の正規化、エスケープ処理を行わない（FR-9 / AC-6）。

**3. 生成パラメータ**

- `seed` / `steps` / `cfg` / `sampler_name` / `scheduler`: サンプラーノードの同名 `inputs` から取得する。リンクの場合は辿って解決する。
- `seed` が存在しない場合は `noise_seed` を代替キーとして参照する（KSamplerAdvanced 系）。

**4. モデル名**

- サンプラーの `model` 入力を辿る。
- 到達したノードの `inputs` に `ckpt_name` があれば採用。なければ `unet_name` を参照する。
- LoRA ローダー等、`model` 入力を持つ中継ノードの場合はさらに辿る。

**5. 生成解像度**

- サンプラーの `latent_image` 入力を辿り、`width` / `height` を持つノードに到達すれば採用する。
- 到達できない場合は未取得とし、実ファイルの解像度で代替しない（両者を混同しないため）。

### 抽出状態の判定（FR-11）

- `none`: `prompt` / `workflow` のいずれも存在しない
- `full`: positive、negative、model_name、seed、steps、cfg、sampler_name、scheduler をすべて取得できた
- `partial`: 上記以外（1項目でも欠けた場合）

`partial` であっても取得できた項目は保存し、生 JSON も保存する（FR-10 / AC-7, AC-8）。

---

## 5. Thumbnail Design

- 出力先: `<scan_root>/<thumbnail_dir_name>/`（フラット構成。サブディレクトリを作らない）
- ファイル名: `YYYYMMDD_HHMMSS_<uuid4先頭8桁>.webp`
  - 例: `20260913_142530_a1b2c3d4.webp`
  - 日時はサムネイル生成時刻（ローカルタイム）
  - 名前は生成時に一意に決まり、以後変更しない。`images.thumbnail_name` に保持する
- 生成条件: `thumbnail_name` が未設定、または `thumbnail_status = 'failed'` の場合のみ生成する。既に `ok` のものは再生成しない（FR-29 / AC-21）
- リサイズ: アスペクト比を維持し、長辺が `thumbnail_max_edge` 以下になるよう縮小する。元画像の長辺が `thumbnail_max_edge` 以下の場合は元寸法のまま出力する（拡大しない）（FR-26 / AC-18）
  - Pillow の `Image.thumbnail()` は拡大を行わないため、この挙動に合致する
  - リサンプリングは `Image.Resampling.LANCZOS`
- 書き出し: `format="WEBP"`, `quality=thumbnail_quality`, `method=4`
  - アルファチャンネルを持つ画像はそのまま WebP のアルファとして保持する
- 元画像の削除・変更は行わない（NFR-2 / AC-19）
- 元画像が `missing` になってもサムネイルファイルは削除しない（FR-31 / AC-22）
- 孤児サムネイル（DB から参照されないファイル）の削除は行わない

---

## 6. Endpoints Design

### 共通

- 認証を持たない。`host` は既定で `127.0.0.1` にバインドし、外部公開しない（NFR-1）。
- 画像バイナリ配信（`/thumbnail`, `/file`）を除き、レスポンスは `application/json`（FR-22 / AC-15）。
- エラーレスポンスは以下の形式で統一する。

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "image not found"
  }
}
```

- エラーコード: `NOT_FOUND` / `INVALID_SCAN_ROOT` / `SCAN_IN_PROGRESS` / `FILE_MISSING` / `THUMBNAIL_UNAVAILABLE` / `VALIDATION_ERROR`

### `POST /scan`

- request body: なし（対象は `config.toml` の `scan_root`）
- flow: §3 の手順を実行し、`scan_runs` の集計を返す
- 実行中の再要求 → `409 SCAN_IN_PROGRESS`
- `scan_root` 不在 → `400 INVALID_SCAN_ROOT`（登録データは一切変更しない）

### `GET /folders`

- 用途: 左ペインのフォルダツリー（FR-33 / FR-34）
- flow:
  - `SELECT dir_path, COUNT(*) FROM images WHERE presence = 'active' GROUP BY dir_path` で直下件数を集計する
  - 取得した `dir_path` を `/` で分割し、アプリケーション側で入れ子構造を組み立てる
  - 中間ディレクトリ（画像を直接持たないが子孫が持つ階層）もノードとして生成する。その `directCount` は 0 とする
  - 各ノードの `totalCount` は、自身の `directCount` と全子孫の `directCount` の合計とする
  - `thumbnail_dir_name` および `.` で始まるセグメントを含むパスは除外する（FR-35 / AC-24）。ただしスキャン時点で既に除外されているため、通常は該当しない
- フォルダ階層は DB のテーブルとして保持せず、毎回 `dir_path` から導出する。フォルダはファイルシステム側の都合で変わるため、二重管理を避ける

### `GET /images`

- query:
  - `cursor` (string, optional) — 直前のレスポンスの `nextCursor`。未指定なら先頭から
  - `limit` (integer, default 100, max 300)
  - `dir` (string, optional) — 絞り込むフォルダの相対パス。空文字列はルート直下
  - `recursive` (boolean, default true) — `dir` 配下のサブフォルダを含めるか（FR-36 / AC-25）
  - `favorite_only` (boolean, default false)
  - `include_missing` (boolean, default false)
- flow:
  - 並び順は `file_mtime DESC, id DESC` に固定する（FR-14 / AC-10）
  - カーソルは `<file_mtime>|<id>` を Base64 URL-safe エンコードした文字列とする
  - 続きの取得は行値比較で行う。`WHERE (file_mtime, id) < (:cursor_mtime, :cursor_id)`
    - ページ番号方式ではないため、スキャンによって件数が変化しても取得済み範囲と重複・欠落しない（FR-37 / AC-26）
  - `recursive = false` のとき `dir_path = :dir`、`true` のとき `dir_path = :dir OR dir_path LIKE :dir || '/%'`（`dir` が空文字列の場合は条件を付けない）
  - `include_missing = false` のとき `presence = 'active'` のみを対象とする（FR-16 / AC-14）
  - `totalCount` は同一条件の `COUNT(*)` を返す（FR-38）。カーソル未指定の初回要求時のみ算出し、2回目以降は `null` を返す
  - 一覧では `positive_prompt` / `negative_prompt` および生 JSON を返さない（転送量抑制のため）
  - 返却件数が `limit` 未満の場合、`nextCursor` は `null` とする

### `GET /images/{id}`

- 詳細1件。ファイル情報、生成パラメータ、プロンプト全文、状態を返す
- 未存在 ID → `404 NOT_FOUND`（FR-23 / AC-15）

### `GET /images/{id}/raw-metadata`

- `image_raw_metadata` の `prompt_json` / `workflow_json` をパース済み JSON として返す（AC-8）
- 該当なし → `404 NOT_FOUND`

### `GET /images/{id}/thumbnail`

- `thumbnail_name` からパスを解決し、`image/webp` で返す
- `presence = 'missing'` でもサムネイルが存在すれば返す（FR-31 / AC-22）
- `thumbnail_status != 'ok'` またはファイル不在 → `404 THUMBNAIL_UNAVAILABLE`
- `Cache-Control: public, max-age=86400` を付与する

### `GET /images/{id}/file`

- 原寸画像を `image/png` で返す
- `presence = 'missing'` またはファイル不在 → `404 FILE_MISSING`

### `PUT /images/{id}/favorite`

- request body: `{"is_favorite": true}`
- `images.is_favorite` と `updated_at` のみを更新する。画像ファイルおよび PNG メタデータへの書き込みを一切行わない（FR-21 / AC-13）
- 冪等とする。同じ値を繰り返し送っても成功を返す

---

## 7. API Contract

### POST /scan

Success Response: `200 OK`

```json
{
  "scanRunId": 12,
  "startedAt": "2026-09-13T05:12:04Z",
  "finishedAt": "2026-09-13T05:14:37Z",
  "scannedCount": 1284,
  "createdCount": 37,
  "updatedCount": 4,
  "missingCount": 2,
  "extractFailedCount": 5,
  "thumbnailGeneratedCount": 37,
  "thumbnailFailedCount": 1,
  "renamedCount": 37
}
```

Invalid Root Error: `400 Bad Request`

```json
{
  "error": {
    "code": "INVALID_SCAN_ROOT",
    "message": "scan root does not exist"
  }
}
```

### GET /folders

Success Response: `200 OK`

```json
{
  "rootTotalCount": 4345,
  "folders": [
    {
      "name": "2026-09",
      "path": "2026-09",
      "directCount": 1839,
      "totalCount": 2104,
      "children": [
        {
          "name": "upscaled",
          "path": "2026-09/upscaled",
          "directCount": 265,
          "totalCount": 265,
          "children": []
        }
      ]
    }
  ]
}
```

### GET /images

Success Response: `200 OK`

```json
{
  "totalCount": 4345,
  "nextCursor": "MjAyNi0wOS0xMlQxMTowMzoyMVp8MTAx",
  "items": [
    {
      "id": 101,
      "fileName": "ComfyUI_00042_.png",
      "filePath": "2026-09/ComfyUI_00042_.png",
      "dirPath": "2026-09",
      "fileSize": 1843200,
      "imageWidth": 1024,
      "imageHeight": 1024,
      "fileMtime": "2026-09-12T11:03:21Z",
      "presence": "active",
      "isFavorite": true,
      "thumbnailUrl": "/images/101/thumbnail",
      "thumbnailStatus": "ok",
      "extractionStatus": "full"
    }
  ]
}
```

`totalCount` は `cursor` 未指定時のみ数値を返し、2回目以降は `null` とする。末尾に到達した場合 `nextCursor` は `null` とする。

### GET /images/{id}

Success Response: `200 OK`

```json
{
  "id": 101,
  "fileName": "ComfyUI_00042_.png",
  "filePath": "2026-09/ComfyUI_00042_.png",
  "fileSize": 1843200,
  "imageWidth": 1024,
  "imageHeight": 1024,
  "fileMtime": "2026-09-12T11:03:21Z",
  "contentHash": "9f2b...",
  "presence": "active",
  "isFavorite": true,
  "thumbnailUrl": "/images/101/thumbnail",
  "fileUrl": "/images/101/file",
  "extractionStatus": "full",
  "generation": {
    "positivePrompt": "masterpiece, 1girl, standing in a field",
    "negativePrompt": "lowres, bad anatomy",
    "modelName": "sd_xl_base_1.0.safetensors",
    "seed": 872341905,
    "steps": 20,
    "cfg": 7.0,
    "samplerName": "euler",
    "scheduler": "normal",
    "genWidth": 1024,
    "genHeight": 1024
  }
}
```

抽出できなかった項目は `null` とする。キー自体は省略しない。

Not Found Error: `404 Not Found`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "image not found"
  }
}
```

### PUT /images/{id}/favorite

Request:

```json
{
  "is_favorite": true
}
```

Success Response: `200 OK`

```json
{
  "id": 101,
  "isFavorite": true
}
```

---

## 8. 決定事項の根拠（変更時に読む箇所）

- **ORM を使わない**: テーブル2〜3個、クエリも単純なため。スキーマ変更時は `db.py` の初期化 SQL を直接編集する。
- **カーソル方式にした**: 無限スクロール中にスキャンが走ると、ページ番号方式では項目のずれによる重複・欠落が発生する。並び順を `file_mtime DESC, id DESC` 固定にしたことで、カーソルは1組の値で表現できる。並び順の切り替えを追加する場合、並び順ごとにカーソル定義と索引が必要になる。
- **フォルダ階層をテーブルで持たない**: フォルダはファイルシステム側の都合で追加・移動されるため、DB に持つと再スキャンのたびに同期処理が必要になる。`dir_path` の `GROUP BY` から毎回導出する。数千件規模ではこの集計で問題ない。
- **`dir_path` を列として分離した**: `file_path` への `LIKE` で代用できるが、フォルダ絞り込みに索引が効かなくなるため独立した列とする。
- **サムネイル長辺を 768px にした**: 一覧セル幅の上限が 320px であり、高 DPI 環境での 2 倍表示（640px）を上回るため。`grid_max_cell` を広げる場合はこの値も見直す（FR-39 / AC-27）。
- **サムネイル名を UUID 由来にした**: 内容ハッシュ由来にすると冪等になるが、指定された命名規則に従う。結果として、DB を破棄して再スキャンすると旧サムネイルが孤児として残る。運用上は `.thumbnails` を手動削除して再スキャンする。
- **`gen_width` と `image_width` を分けた**: アップスケールノードを挟むと両者は一致しない。プロンプト再利用時に必要なのは `gen_width` のため、混同しないよう別列とする。
- **リネームをハッシュ算出より前に行う**: リネーム後のパスで登録・更新するため、登録済み画像のリネームは FR-4 のパス更新として扱われ、`updated_count` にも数えられる。元ファイル名は保持しない（必要になれば `original_name` 列の追加を検討する）。
- **一覧でプロンプトを返さない**: 1件あたりのプロンプトが長く、100件分を返すとレスポンスが肥大するため。プロンプト検索を追加する場合はここを見直す。

---

## 9. Frontend Layout

3ペイン構成とする。

- **左ペイン**: 固定項目（すべて / お気に入り / 見つからない）と、`GET /folders` から構築するフォルダツリー。各項目に件数を表示する。
- **中央ペイン**: サムネイルのグリッド。CSS Grid の `grid-template-columns: repeat(auto-fill, minmax(<cell>px, 1fr))` とし、`<cell>` をスライダーで `grid_min_cell` 〜 `grid_max_cell` の範囲で変更する。
  - 末尾付近までスクロールした時点で `nextCursor` を用いて次を取得する（IntersectionObserver）。
  - 絞り込み条件を変更した際はカーソルと取得済み項目を破棄して先頭から取り直す。
- **右ペイン**: 選択中画像の情報。`GET /images/{id}` の結果を表示する。
  - positive / negative プロンプトはそれぞれ独立したコピーボタンを持つ。コピー対象は API が返した文字列そのものとし、表示上の整形を反映しない（FR-9）。
  - `extractionStatus` が `partial` / `none` の場合、取得できなかった項目である旨を表示する。生メタデータへのリンクを置く。
