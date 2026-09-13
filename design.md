# design.md

前提: [requirements.md](./requirements.md) の FR-1 〜 FR-52 / AC-1 〜 AC-38 を満たす。

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
- アップロード受け取り: python-multipart（タグ CSV の受け取りにのみ使用）
- DB: SQLite（標準ライブラリ `sqlite3` を直接使用。ORM は導入しない）。辞書の全列検索に FTS5 の trigram トークナイザを使う

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
  - `lora_root`: LoRA ファイルのルートフォルダ絶対パス（任意。未設定なら LoRA はワークフローからのみ登録される）

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
├── lora_scanner.py      # LoRA フォルダ走査と画像との関連付け（FR-41 / FR-42）
├── prompt_tokens.py     # プロンプトの正規化と語の切り出し（FR-46）
├── tag_importer.py      # タグ CSV の取り込みと関連付けの再構築（FR-45 / FR-48）
├── folders.py           # dir_path からのフォルダツリー導出
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

### `loras`

- 用途: LoRA 1 件の情報と利用者メモ（FR-41 / FR-43）
- columns:
  - `id` (INTEGER, PK, AUTOINCREMENT)
  - `name` (TEXT, NOT NULL, UNIQUE) — ComfyUI の `lora_name` と同じ `lora_root` からの相対パス。区切りは `/`
  - `file_name` (TEXT, NOT NULL)
  - `file_size` (INTEGER) / `file_mtime` (TEXT) — ファイル未検出なら NULL
  - `presence` (TEXT, NOT NULL) — `active` / `missing` / `unknown`（ワークフローでのみ検出）
  - `trigger_words` (TEXT, NOT NULL, default '') / `memo` (TEXT, NOT NULL, default '')
  - `created_at` / `updated_at` (TEXT, NOT NULL)

### `image_loras`

- 用途: 画像と LoRA の多対多（FR-42）
- columns: `image_id` (FK images, CASCADE), `lora_id` (FK loras, CASCADE), `strength_model` (REAL), `strength_clip` (REAL)。PK は `(image_id, lora_id)`
- indexes: `idx_image_loras_lora (lora_id)`

### `tags`

- 用途: 辞書 1 件（FR-45 / FR-49）
- columns:
  - `id` (INTEGER, PK, AUTOINCREMENT)
  - `name` (TEXT, NOT NULL, UNIQUE) — CSV の `tag` 列の原文。アンダースコア区切りのまま保持する
  - `name_normalized` (TEXT, NOT NULL, UNIQUE) — FR-46 の正規化を適用した形。照合はこの列で行う
  - `category` (INTEGER) / `category_name` (TEXT, NOT NULL) — LoRA 由来の語は `category` が NULL で `category_name = 'lora'`
  - `post_count` (INTEGER, NOT NULL, default 0) — 検索結果と候補の並び順に使う
  - `tag_created_at` (TEXT) — CSV の `created_at` 列。レコードの `created_at` と区別する
  - `aliases` (TEXT, NOT NULL, default '') / `other_names` (TEXT, NOT NULL, default '') — CSV の原文をそのまま保持する
  - `posts_url` (TEXT) / `wiki_url` (TEXT) / `has_wiki` (TEXT)
  - `source` (TEXT, NOT NULL) — `csv` / `lora`
  - `lora_id` (INTEGER) — `source = 'lora'` のとき登録元。`loras.id` を参照し `ON DELETE SET NULL`
  - `created_at` / `updated_at` (TEXT, NOT NULL)
- indexes:
  - `uk_tags_name (name)` unique / `uk_tags_name_normalized (name_normalized)` unique
  - `idx_tags_post_count (post_count DESC)`
- `source = 'csv'` の行だけが CSV 取り込みで置き換えられる。`source = 'lora'` の行は残す（FR-49）

### `tag_aliases`

- 用途: 別名から正式タグへの解決（FR-47）
- columns: `alias_normalized` (TEXT, NOT NULL), `tag_id` (INTEGER, NOT NULL, FK tags CASCADE)。PK は `(alias_normalized, tag_id)`
- 照合時は `tags.name_normalized` を先に引き、見つからない場合にのみこの表を引く。これで正式名が別名より優先される

### `tags_fts`

- 用途: 辞書の全列検索（FR-51）
- FTS5 の外部コンテンツ表。`content='tags'`, `content_rowid='id'`, `tokenize='trigram'`
- 対象列: `name`, `name_normalized`, `aliases`, `other_names`, `category_name`
- trigram を選んだ理由は、日本語を含む部分一致が索引で引けるため。詳細は §9

### `image_prompt_tokens`

- 用途: 画像のプロンプトから切り出した正規化済みの語（FR-48）
- columns: `image_id` (INTEGER, NOT NULL, FK images CASCADE), `side` (TEXT, NOT NULL, `positive` / `negative`), `position` (INTEGER, NOT NULL), `token` (TEXT, NOT NULL)。PK は `(image_id, side, position)`
- indexes: `idx_image_prompt_tokens_token (token)`
- 辞書に無い語も保持する。辞書が増えたとき、画像を読み直さずに関連付けを作り直せる

### `image_tags`

- 用途: 画像と辞書タグの関連（FR-48）
- columns: `image_id` (INTEGER, NOT NULL, FK images CASCADE), `tag_id` (INTEGER, NOT NULL, FK tags CASCADE)。PK は `(image_id, tag_id)`
- indexes: `idx_image_tags_tag (tag_id, image_id)`
- `side = 'positive'` の語からのみ作る。この表は `image_prompt_tokens` と `tags` の結合結果であり、いつでも作り直せる

### `tag_imports`

- 用途: CSV 取り込みの結果記録
- columns: `id` (INTEGER, PK, AUTOINCREMENT), `started_at` / `finished_at` (TEXT), `file_name` (TEXT), `read_count` / `imported_count` / `skipped_count` / `alias_count` / `linked_image_count` (INTEGER, NOT NULL, default 0), `error` (TEXT)

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

## 6. Tag Dictionary Design

### 正規化（FR-46）

プロンプト文字列から語を切り出す手順。辞書側の `name` には手順 4 〜 6 のみを適用して `name_normalized` を作る。辞書名に含まれる括弧は意味を持つ文字なので取り除かない。

1. カンマで分割する。
2. 各断片について、重み指定 `(語:1.2)` と強調の括弧 `(語)` `[語]` `{語}` を取り除き、中身を取り出す。入れ子は内側まで解く。
3. エスケープされた括弧 `\(` `\)` を通常の括弧に戻す。この段階で `hatsune_miku_(cosplay)` のようなタグ側の括弧と形が揃う。
4. アンダースコアを半角空白に置き換える。
5. 英字を小文字にする。
6. 前後の空白を取り除く。空文字列になったものは捨てる。

`<lora:...>` の記法、`BREAK`、埋め込み名は語として扱わない。表記ゆれの吸収は行わない。

### 照合（FR-47 / FR-48）

1. positive プロンプトと negative プロンプトの双方を上記で正規化し、`image_prompt_tokens` に出現順で保存する。
2. `side = 'positive'` の語について `tags.name_normalized` と完全一致で引く。
3. 見つからない語のみ `tag_aliases.alias_normalized` を引き、見つかれば正式タグとする。これにより正式名が別名より優先される。
4. 一致したものを `image_tags` に入れる。一致しなかった語はそのまま `image_prompt_tokens` に残り、画像詳細で「辞書に無い語」として表示できる。

関連付けの作り直しは、`image_prompt_tokens` と `tags` の結合で一括して行う。画像ファイルも生 JSON も読み直さない。

### 取り込み（FR-45）

`POST /tags/import` の処理順序。画像スキャンと同じロックを共有し、実行中の再要求は `409` を返す。

1. アップロードされた CSV をヘッダ行付きとして読む。想定した列が無ければ `400 INVALID_TAG_CSV` を返し、辞書を変更しない。
2. `source = 'csv'` の行と、それに紐づく別名を削除する。`source = 'lora'` の行は残す。
3. 1 行ずつ正規化して挿入する。解釈できない行は飛ばして件数に数える。`source = 'lora'` の行と `name_normalized` が衝突した場合は CSV 側を優先し、既存の行を `csv` に切り替えて登録元の LoRA への参照を残す。
4. `aliases` を分割して `tag_aliases` を作る。正式名として既に存在する別名は入れない。
5. FTS5 の索引を作り直す。
6. `image_prompt_tokens` と結合して `image_tags` を作り直す。
7. 集計を `tag_imports` に記録して返す。

### LoRA トリガーワードの登録（FR-49）

`PUT /loras/{id}` で Trigger Words が保存されたとき、カンマで分割した各語を正規化して辞書へ登録する。

- 既に同じ `name_normalized` の行があれば登録しない。CSV 由来の語と重複した場合は CSV 側をそのまま使う。
- 新たに登録した語については、その語を持つ画像だけを `image_prompt_tokens` から引いて `image_tags` に追加する。辞書全体との結合は行わない。
- LoRA から語が取り除かれても辞書からは消さない。その語で絞り込んだ一覧が突然変わることを避けるためで、不要になった語は辞書側で削除する。

### 規模（NFR-5）

1,081,663 行の CSV と画像 5000 件で、実装した取り込み処理を通して測った値。

| 処理 | 実測 |
|---|---|
| 取り込み全体（初回） | 37 秒 |
| 取り込み全体（入れ替え） | 36 秒 |
| 内訳: CSV の読み取りと中間表への蓄積 | 8 秒 |
| 内訳: 辞書表への整列挿入と索引再作成 | 7 秒 |
| 内訳: 別名表の作成 | 2 秒 |
| 内訳: FTS5 索引の作成 | 9 秒 |
| 内訳: 画像との関連付け | 2 〜 7 秒 |
| 内訳: 旧 CSV 行の削除（入れ替え時） | 7 秒 |
| 画像 5000 件の関連付け再構築（単独） | 1.7 秒 |
| 辞書の全列検索 3 回 | 90 ミリ秒 |
| 辞書が占める容量 | 約 530 メガバイト |

容量の内訳は辞書本体が 244 メガバイト、全文検索の索引が 220 メガバイト、残りが名前の一意索引と別名である。URL 列を持つ分だけ本体が大きい。取り込みは画像スキャンと同じく同期エンドポイントで行い、画面側は処理中の表示で待つ。

---

## 7. Endpoints Design

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

- エラーコード: `NOT_FOUND` / `INVALID_SCAN_ROOT` / `INVALID_LORA_ROOT` / `INVALID_TAG_CSV` / `SCAN_IN_PROGRESS` / `FILE_MISSING` / `THUMBNAIL_UNAVAILABLE` / `VALIDATION_ERROR`

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

### `GET /loras`

- 全 LoRA を `name` 昇順で返す。各項目に `imageCount`（active な使用画像数）を含む。

### `POST /loras/scan`

- `lora_root` が設定されていれば配下の `.safetensors` / `.pt` / `.ckpt` を走査し、`name` で upsert、`file_size` / `file_mtime` / `presence='active'` を更新、出現しなかった `active` を `missing` にする。`lora_root` 不在 → `400 INVALID_LORA_ROOT`
- 続けて `image_raw_metadata.prompt_json` を持つ全画像について `lora_name` を持つノードを抽出し `image_loras` を張り直す（登録済み画像のバックフィル）
- 画像スキャンと同じロックを使い、実行中は `409 SCAN_IN_PROGRESS`
- 応答: `loraRootConfigured`, `scannedFileCount`, `fileCreatedCount`, `fileMissingCount`, `backfilledImageCount`, `linkedLoraCount`

### `GET /loras/{id}` / `PUT /loras/{id}`

- `PUT` の body は `{"trigger_words": string?, "memo": string?}`。指定したキーのみ更新し、更新後の LoRA を返す。未存在 → `404 NOT_FOUND`

### `GET /images` の追加 query

- `lora` (integer, optional) — その LoRA を使う画像のみ（`id IN (SELECT image_id FROM image_loras WHERE lora_id = ?)`）

### `GET /images/{id}` の追加項目

- `loras`: `[{"id", "name", "presence", "triggerWords", "strengthModel", "strengthClip"}]`

### `POST /tags/import`

- request: `multipart/form-data` の `file`。ヘッダ行付きの CSV（FR-45）
- flow: §6「取り込み」の手順。画像スキャンと同じロックを共有する
- 列が想定と異なる → `400 INVALID_TAG_CSV`（辞書は変更しない）。実行中の再要求 → `409 SCAN_IN_PROGRESS`
- 受け入れる上限を超えたファイル → `400 VALIDATION_ERROR`

### `GET /tags/search`

- query: `q`（部分一致。全列が対象）, `limit`（既定 50、最大 200）, `category`（任意）, `source`（任意）
- flow: `tags_fts` を MATCH で引き、`post_count` の降順に返す（FR-51）
- `q` は FR-46 の正規化を適用してから引く。利用者が空白区切りで入力してもアンダースコア区切りの名前に当たるようにするため
- `q` が空 → 投稿数の多い順に `limit` 件を返す

### `GET /tags/used`

- 用途: 左ペインの一覧。登録画像で実際に使われているタグのみを返す
- query: `limit`（既定 200、最大 1000）
- flow: `image_tags` を集計し、`presence = 'active'` の画像の件数が多い順に返す

### `GET /tags/{id}`

- 辞書 1 件の全項目と、そのタグを使う画像の件数を返す（FR-52）
- `source = 'lora'` の場合は登録元の LoRA を含める

### `GET /images` の追加 query（タグ）

- `tag` (integer, 繰り返し可) — 絞り込むタグ。未指定なら条件を付けない
- `tag_match` (string, `and` / `or`、既定 `and`) — すべてを含むか、いずれかを含むか（FR-50）
- `and` は `image_tags` を `GROUP BY` して `HAVING COUNT(DISTINCT tag_id)` が指定数に等しい行を採る。画像側から `EXISTS` を連ねる書き方より速い。根拠は §9
- 他の絞り込み条件とは AND で組み合わせる

### `GET /images/{id}` の追加項目（タグ）

- `promptTokens`: positive プロンプトの語を出現順に並べたもの。`[{"token", "tag"}]` とし、辞書に無い語は `tag` を `null` とする
- `tag` の中身は `{"id", "name", "category", "categoryName", "postCount", "aliases", "otherNames", "wikiUrl", "source", "loraId"}`

### `PUT /images/{id}/favorite`

- request body: `{"is_favorite": true}`
- `images.is_favorite` と `updated_at` のみを更新する。画像ファイルおよび PNG メタデータへの書き込みを一切行わない（FR-21 / AC-13）
- 冪等とする。同じ値を繰り返し送っても成功を返す

---

## 8. API Contract

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

### POST /tags/import

Success Response: `200 OK`

```json
{
  "tagImportId": 3,
  "startedAt": "2026-09-13T06:02:11Z",
  "finishedAt": "2026-09-13T06:02:36Z",
  "fileName": "danbooru_tags.csv",
  "readCount": 1081663,
  "importedCount": 1081661,
  "skippedCount": 2,
  "aliasCount": 195383,
  "linkedImageCount": 4821
}
```

Invalid CSV Error: `400 Bad Request`

```json
{
  "error": {
    "code": "INVALID_TAG_CSV",
    "message": "required column is missing: post_count"
  }
}
```

### GET /tags/search

Success Response: `200 OK`

```json
{
  "items": [
    {
      "id": 12,
      "name": "long_hair",
      "category": 0,
      "categoryName": "general",
      "postCount": 5123456,
      "aliases": "longhair",
      "otherNames": "ロングヘア, 長髪",
      "postsUrl": "https://danbooru.donmai.us/posts?tags=long_hair",
      "wikiUrl": "https://danbooru.donmai.us/wiki_pages/long_hair",
      "hasWiki": "yes",
      "source": "csv",
      "loraId": null,
      "imageCount": 312
    }
  ]
}
```

### GET /images/{id} のタグ部分

```json
{
  "promptTokens": [
    {
      "token": "masterpiece",
      "tag": {"id": 3, "name": "masterpiece", "category": 5, "categoryName": "meta", "postCount": 812345, "source": "csv", "loraId": null}
    },
    {
      "token": "long hair",
      "tag": {"id": 12, "name": "long_hair", "category": 0, "categoryName": "general", "postCount": 5123456, "source": "csv", "loraId": null}
    },
    {"token": "sksartstyle", "tag": {"id": 900, "name": "sksartstyle", "category": null, "categoryName": "lora", "postCount": 0, "source": "lora", "loraId": 4}},
    {"token": "an entirely made up phrase", "tag": null}
  ]
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

## 9. 決定事項の根拠（変更時に読む箇所）

- **ORM を使わない**: テーブル2〜3個、クエリも単純なため。スキーマ変更時は `db.py` の初期化 SQL を直接編集する。
- **カーソル方式にした**: 無限スクロール中にスキャンが走ると、ページ番号方式では項目のずれによる重複・欠落が発生する。並び順を `file_mtime DESC, id DESC` 固定にしたことで、カーソルは1組の値で表現できる。並び順の切り替えを追加する場合、並び順ごとにカーソル定義と索引が必要になる。
- **フォルダ階層をテーブルで持たない**: フォルダはファイルシステム側の都合で追加・移動されるため、DB に持つと再スキャンのたびに同期処理が必要になる。`dir_path` の `GROUP BY` から毎回導出する。数千件規模ではこの集計で問題ない。
- **`dir_path` を列として分離した**: `file_path` への `LIKE` で代用できるが、フォルダ絞り込みに索引が効かなくなるため独立した列とする。
- **サムネイル長辺を 768px にした**: 一覧セル幅の上限が 320px であり、高 DPI 環境での 2 倍表示（640px）を上回るため。`grid_max_cell` を広げる場合はこの値も見直す（FR-39 / AC-27）。
- **サムネイル名を UUID 由来にした**: 内容ハッシュ由来にすると冪等になるが、指定された命名規則に従う。結果として、DB を破棄して再スキャンすると旧サムネイルが孤児として残る。運用上は `.thumbnails` を手動削除して再スキャンする。
- **`gen_width` と `image_width` を分けた**: アップスケールノードを挟むと両者は一致しない。プロンプト再利用時に必要なのは `gen_width` のため、混同しないよう別列とする。
- **リネームをハッシュ算出より前に行う**: リネーム後のパスで登録・更新するため、登録済み画像のリネームは FR-4 のパス更新として扱われ、`updated_count` にも数えられる。元ファイル名は保持しない（必要になれば `original_name` 列の追加を検討する）。
- **タグの照合を完全一致に限った**: 表記ゆれの吸収や部分一致は、誤った関連付けを生んだときに利用者が原因を追えない。正規化の規則を FR-46 の 6 手順に固定し、当たらない語は「辞書に無い語」として画面に出すほうが、追加すべき語がはっきりする。
- **画像側に正規化済みの語を保存する**: 一致した組み合わせだけを保存すると、辞書へ語を 1 つ足すたびに全画像のプロンプトを解析し直すことになる。語を保存しておけば辞書との結合だけで済み、画像 5000 件で 0.5 秒だった。LoRA のトリガーワードを日常的に登録する使い方が前提なので、この差が効く。
- **辞書の検索に FTS5 の trigram を使う**: 全列に部分一致をかけると 1,081,663 行で 229 〜 387 ミリ秒かかり、入力のたびに引く用途に耐えない。trigram の索引なら 50 〜 63 ミリ秒で、日本語の部分一致も引ける。代償は索引の 141 メガバイトと、取り込み時の 10 秒である。前方一致に限ればこの索引は不要になるので、容量を切り詰める必要が出たらここを見直す。
- **タグの AND を GROUP BY で書く**: 画像側から `EXISTS` を連ねると、画像 5 万件で 33.9 ミリ秒かかった。`image_tags` の索引から出発して `GROUP BY` と `HAVING` で絞ると 0.9 ミリ秒だった。タグの絞り込みは件数が少ないほうから辿る。
- **LoRA 絞り込みとタグ絞り込みを両方持つ**: 前者はワークフローの `lora_name` を根拠に「その LoRA で生成した画像」を返し、後者はプロンプトの文字列を根拠に「その語を書いた画像」を返す。LoRA を読み込んだが語を書かなかった画像、語だけ使い回して LoRA を外した画像で結果が食い違う。どちらも意味のある問いなので片方に寄せない。
- **一覧でプロンプトを返さない**: 1件あたりのプロンプトが長く、100件分を返すとレスポンスが肥大するため。プロンプト検索を追加する場合はここを見直す。

---

## 10. Frontend Layout

3ペイン構成とする。

- **左ペイン**: 固定項目（すべて / お気に入り / 見つからない）と、`GET /folders` から構築するフォルダツリー。各項目に件数を表示する。
  - 表示・フォルダ・LoRA・タグの 4 区画は個別に折りたためる。見出しに区画内の件数を出し、折りたたんでいても中身の量が分かるようにする。開閉の状態は `localStorage` に保存する。
  - 左ペインと中央ペインの境界をドラッグして幅を変えられる。既定 280px、下限 180px、上限 560px。幅は `localStorage` に保存し、境界のダブルクリックで既定に戻す。LoRA 名やタグ名は幅が狭いと切れるため、利用者が調整できるようにする。
- **中央ペイン**: サムネイルのグリッド。CSS Grid の `grid-template-columns: repeat(auto-fill, minmax(<cell>px, 1fr))` とし、`<cell>` をスライダーで `grid_min_cell` 〜 `grid_max_cell` の範囲で変更する。
  - 末尾付近までスクロールした時点で `nextCursor` を用いて次を取得する（IntersectionObserver）。
  - 絞り込み条件を変更した際はカーソルと取得済み項目を破棄して先頭から取り直す。
- **左ペイン（タグ）**: 「タグ CSV を取り込む」ボタンと、`GET /tags/used` による使用中タグの一覧。件数付きで表示する。辞書全体は検索欄から `GET /tags/search` で引く。辞書は 100 万件規模なので一覧には出さない。
  - タグは複数選択でき、選択中のものを上部に並べる。「すべて含む」と「いずれかを含む」を切り替えるトグルを置く。
  - 区分ごとに色を変える。`source = 'lora'` の語は独自の区分として扱い、選択すると右ペインに登録元の LoRA への導線を出す。
- **左ペイン（LoRA）**: 「LoRA スキャン」ボタンと `GET /loras` の一覧（使用件数、`missing` / `unknown` バッジ）。選択すると一覧を `lora` で絞り込み、右ペインに LoRA エディタ（ファイル情報、Trigger Words、メモ、保存、Trigger Words のコピー）を表示する。
- **右ペイン**: 選択中画像の情報。使用 LoRA（strength と Trigger Words、コピーボタン）を表示し、LoRA 名から LoRA エディタへ移動できる。`GET /images/{id}` の結果を表示する。
  - positive / negative プロンプトはそれぞれ独立したコピーボタンを持つ。コピー対象は API が返した文字列そのものとし、表示上の整形を反映しない（FR-9）。
  - `extractionStatus` が `partial` / `none` の場合、取得できなかった項目である旨を表示する。生メタデータへのリンクを置く。
  - positive プロンプトは原文の表示とは別に、`promptTokens` を語ごとの一覧としても表示する。各語に区分・投稿数・和名を添え、選ぶとそのタグで一覧を絞り込む。辞書に無い語はその旨を示し、LoRA のトリガーワード由来の語からは LoRA の画面へ移動できる。
