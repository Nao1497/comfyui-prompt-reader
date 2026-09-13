"""FastAPI application factory and routes (design.md §6 / §7)."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, StrictBool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db, folders, repository, scanner
from app.config import AppConfig
from app.models import Image, ScanRun

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


class ApiError(Exception):
    """An error rendered as ``{"error": {"code", "message"}}`` (design §6 共通)."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def not_found(message: str = "image not found") -> ApiError:
    return ApiError(404, "NOT_FOUND", message)


class _DbHandle:
    def __init__(self, lock: threading.Lock, conn: sqlite3.Connection):
        self._lock = lock
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self._lock.acquire()
        return self._conn

    def __exit__(self, *exc) -> None:
        self._lock.release()


def create_app(config: AppConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.conn = db.open_database(config.db_path)
        try:
            yield
        finally:
            app.state.conn.close()

    app = FastAPI(title="comfyui-prompt-reader", lifespan=lifespan)
    app.state.config = config
    app.state.db_lock = threading.Lock()
    app.state.scan_lock = threading.Lock()

    _register_error_handlers(app)
    _register_routes(app)
    # 確認事項 #14: the static front end is mounted at "/" after every API route,
    # so "/" serves index.html and "/app.js" the script without shadowing the API.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        # 確認事項 #9: validation errors are 422 VALIDATION_ERROR.
        return error_response(422, "VALIDATION_ERROR", _format_validation(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error", exc_info=exc)
        return error_response(500, "INTERNAL_ERROR", "internal server error")


def _format_validation(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts) or "validation error"


def scan_run_to_json(run: ScanRun) -> dict:
    return {
        "scanRunId": run.id,
        "startedAt": run.started_at,
        "finishedAt": run.finished_at,
        "scannedCount": run.scanned_count,
        "createdCount": run.created_count,
        "updatedCount": run.updated_count,
        "missingCount": run.missing_count,
        "extractFailedCount": run.extract_failed_count,
        "thumbnailGeneratedCount": run.thumbnail_generated_count,
        "thumbnailFailedCount": run.thumbnail_failed_count,
    }


# --- cursor -------------------------------------------------------------------

def encode_cursor(file_mtime: str, image_id: int) -> str:
    raw = f"{file_mtime}|{image_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
        mtime, _, id_text = raw.partition("|")
        if not mtime or not id_text:
            raise ValueError("cursor has no separator")
        return mtime, int(id_text)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ApiError(422, "VALIDATION_ERROR", "invalid cursor") from exc


class FavoriteBody(BaseModel):
    is_favorite: StrictBool


def _parse_or_raw(text: str | None):
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


# --- serialisation ------------------------------------------------------------

def list_item_to_json(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "fileName": row["file_name"],
        "filePath": row["file_path"],
        "dirPath": row["dir_path"],
        "fileSize": row["file_size"],
        "imageWidth": row["image_width"],
        "imageHeight": row["image_height"],
        "fileMtime": row["file_mtime"],
        "presence": row["presence"],
        "isFavorite": bool(row["is_favorite"]),
        "thumbnailUrl": f"/images/{row['id']}/thumbnail",
        "thumbnailStatus": row["thumbnail_status"],
        "extractionStatus": row["extraction_status"],
    }


def image_to_json(img: Image) -> dict:
    return {
        "id": img.id,
        "fileName": img.file_name,
        "filePath": img.file_path,
        "dirPath": img.dir_path,
        "fileSize": img.file_size,
        "imageWidth": img.image_width,
        "imageHeight": img.image_height,
        "fileMtime": img.file_mtime,
        "contentHash": img.content_hash,
        "presence": img.presence,
        "isFavorite": img.is_favorite,
        "thumbnailUrl": f"/images/{img.id}/thumbnail",
        "thumbnailStatus": img.thumbnail_status,
        "fileUrl": f"/images/{img.id}/file",
        "extractionStatus": img.extraction_status,
        "generation": {
            "positivePrompt": img.positive_prompt,
            "negativePrompt": img.negative_prompt,
            "modelName": img.model_name,
            "seed": img.seed,
            "steps": img.steps,
            "cfg": img.cfg,
            "samplerName": img.sampler_name,
            "scheduler": img.scheduler,
            "genWidth": img.gen_width,
            "genHeight": img.gen_height,
        },
    }


def _register_routes(app: FastAPI) -> None:
    def with_db(request: Request):
        """Context manager serialising access to the shared read connection."""
        return _DbHandle(request.app.state.db_lock, request.app.state.conn)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/folders")
    def get_folders(request: Request):
        config: AppConfig = request.app.state.config
        with with_db(request) as conn:
            counts = repository.count_active_by_dir(conn)
            favorite_count = repository.count_favorites_active(conn)
            missing_count = repository.count_missing(conn)
        tree, root_total = folders.build_tree(counts, config.thumbnail_dir_name)
        return {
            "rootTotalCount": root_total,
            # 確認事項 #10(b): counts for the fixed left-pane items.
            "favoriteCount": favorite_count,
            "missingCount": missing_count,
            "folders": tree,
        }

    @app.get("/images")
    def get_images(
        request: Request,
        cursor: str | None = None,
        limit: int = Query(100, ge=1, le=300),
        favorite_only: bool = False,
        include_missing: bool = False,
        missing_only: bool = False,
        dir: str | None = None,
        recursive: bool = True,
    ):
        # 確認事項 #10(a): missing_only is an addition for the "見つからない" pane.
        # 確認事項 #8: dir omitted -> no folder filter; dir="" -> root (see repository).
        filters: dict = {
            "favorite_only": favorite_only,
            "include_missing": include_missing,
            "missing_only": missing_only,
            "dir": dir.strip("/") if dir is not None else None,
            "recursive": recursive,
        }
        decoded = decode_cursor(cursor) if cursor is not None else None
        with with_db(request) as conn:
            total = None if decoded is not None else repository.count_images_filtered(conn, **filters)
            rows = repository.list_images(conn, limit, decoded, **filters)
        items = [list_item_to_json(r) for r in rows]
        next_cursor = None
        if len(items) == limit:
            last = rows[-1]
            next_cursor = encode_cursor(last["file_mtime"], last["id"])
        return {"totalCount": total, "nextCursor": next_cursor, "items": items}

    @app.get("/images/{image_id}")
    def get_image(request: Request, image_id: int):
        with with_db(request) as conn:
            img = repository.get_image(conn, image_id)
        if img is None:
            raise not_found()
        return image_to_json(img)

    @app.get("/images/{image_id}/thumbnail")
    def get_thumbnail(request: Request, image_id: int):
        """FR-30/31: served even when the original is missing, as long as the file exists."""
        config: AppConfig = request.app.state.config
        with with_db(request) as conn:
            img = repository.get_image(conn, image_id)
        if img is None:
            raise not_found()
        if img.thumbnail_status != "ok" or not img.thumbnail_name:
            raise ApiError(404, "THUMBNAIL_UNAVAILABLE", "thumbnail is not available")
        path = config.thumbnail_dir / img.thumbnail_name
        if not path.is_file():
            raise ApiError(404, "THUMBNAIL_UNAVAILABLE", "thumbnail file is missing")
        return FileResponse(
            path, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"}
        )

    @app.get("/images/{image_id}/file")
    def get_file(request: Request, image_id: int):
        """FR-18: the original PNG."""
        config: AppConfig = request.app.state.config
        with with_db(request) as conn:
            img = repository.get_image(conn, image_id)
        if img is None:
            raise not_found()
        path = config.scan_root / img.file_path
        if img.presence == "missing" or not path.is_file():
            raise ApiError(404, "FILE_MISSING", "image file is missing")
        return FileResponse(path, media_type="image/png")

    @app.get("/images/{image_id}/raw-metadata")
    def get_raw_metadata(request: Request, image_id: int):
        """AC-8: parsed prompt/workflow JSON. 確認事項 #13: {"prompt", "workflow"}; unparsable -> raw string."""
        with with_db(request) as conn:
            raw = repository.get_raw_metadata(conn, image_id)
        if raw is None:
            raise not_found("raw metadata not found")
        return {"prompt": _parse_or_raw(raw.prompt_json), "workflow": _parse_or_raw(raw.workflow_json)}

    @app.put("/images/{image_id}/favorite")
    def put_favorite(request: Request, image_id: int, body: FavoriteBody):
        """FR-19/20/21: only images.is_favorite and updated_at change; files are never touched."""
        with with_db(request) as conn:
            updated = repository.set_favorite(conn, image_id, body.is_favorite, scanner.utc_now())
            conn.commit()
        if not updated:
            raise not_found()
        return {"id": image_id, "isFavorite": body.is_favorite}

    @app.post("/scan")
    def post_scan(request: Request):
        """Run a scan synchronously (sync endpoint -> threadpool, so other requests still serve)."""
        config: AppConfig = request.app.state.config
        scan_lock: threading.Lock = request.app.state.scan_lock
        if not scan_lock.acquire(blocking=False):
            raise ApiError(409, "SCAN_IN_PROGRESS", "a scan is already running")
        try:
            if not config.scan_root.is_dir():
                raise ApiError(400, "INVALID_SCAN_ROOT", "scan root does not exist")
            # The scan uses its own connection so reads on app.state.conn are not
            # blocked for the duration of the scan (WAL allows concurrent readers).
            conn = db.connect(config.db_path)
            try:
                run = scanner.run_scan(config, conn)
            finally:
                conn.close()
            return scan_run_to_json(run)
        finally:
            scan_lock.release()
