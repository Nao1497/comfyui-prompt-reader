"""FastAPI application factory and routes (design.md §6 / §7)."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db, scanner
from app.config import AppConfig
from app.models import ScanRun

log = logging.getLogger(__name__)


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


def _register_routes(app: FastAPI) -> None:
    @app.get("/health")
    def health():
        return {"status": "ok"}

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
