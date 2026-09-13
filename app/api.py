"""FastAPI application factory and routes (design.md §6 / §7)."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db
from app.config import AppConfig

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


def _register_routes(app: FastAPI) -> None:
    @app.get("/health")
    def health():
        return {"status": "ok"}
