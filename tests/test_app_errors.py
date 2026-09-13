from app.api import ApiError, create_app


def _add_route(app, path, fn):
    """Register a test route ahead of the catch-all static mount."""
    app.get(path)(fn)
    mount = next(r for r in app.router.routes if getattr(r, "name", "") == "static")
    app.router.routes.remove(mount)
    app.router.routes.append(mount)


def _assert_error(resp, status, code):
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)


def test_unknown_route_is_json_404(client):
    _assert_error(client.get("/no-such-route"), 404, "NOT_FOUND")


def test_validation_error_is_json(config):
    from fastapi.testclient import TestClient

    app = create_app(config)

    def needs_int(n: int):
        return {"n": n}

    _add_route(app, "/needs-int", needs_int)
    with TestClient(app) as c:
        _assert_error(c.get("/needs-int?n=abc"), 422, "VALIDATION_ERROR")


def test_api_error_is_json(config):
    from fastapi.testclient import TestClient

    app = create_app(config)

    def boom():
        raise ApiError(409, "SCAN_IN_PROGRESS", "scan is already running")

    _add_route(app, "/boom", boom)
    with TestClient(app) as c:
        _assert_error(c.get("/boom"), 409, "SCAN_IN_PROGRESS")


def test_unhandled_exception_is_json(config):
    from fastapi.testclient import TestClient

    app = create_app(config)

    def crash():
        raise RuntimeError("nope")

    _add_route(app, "/crash", crash)
    with TestClient(app, raise_server_exceptions=False) as c:
        _assert_error(c.get("/crash"), 500, "INTERNAL_ERROR")


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
