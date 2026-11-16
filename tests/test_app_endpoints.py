from fastapi.responses import FileResponse, JSONResponse
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import Settings, TierLimit
from app.main import create_app


class FakeRedis:
    def __init__(self, available=True):
        self.available = available

    def register_script(self, _script):
        async def unused_script(*_args, **_kwargs):
            return [1, 0]
        return unused_script

    def ping(self):
        if not self.available:
            return ImmediateAwaitable(error=RedisConnectionError("Redis unavailable"))
        return ImmediateAwaitable(value=True)


class ImmediateAwaitable:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def __await__(self):
        if self.error:
            raise self.error
        if False:
            yield None
        return self.value


def make_app(redis):
    settings = Settings(
        "redis://test/0", 0.1, 0.1, "closed", {"test-key": "free"},
        {"free": TierLimit(2, 1)}, ("http://testserver",),
    )
    return create_app(settings, redis_client=redis)


def endpoint(app, path):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("test coroutine unexpectedly yielded")


def test_root_serves_dashboard_and_operational_routes():
    app = make_app(FakeRedis())
    dashboard = run(endpoint(app, "/")())
    assert isinstance(dashboard, FileResponse)
    assert dashboard.path.name == "index.html"
    assert {route.path for route in app.routes} >= {"/docs", "/health", "/ready", "/metrics"}
    assert run(endpoint(app, "/ready")()) == {"status": "ready", "redis": "ok"}


def test_ready_returns_503_when_redis_is_unavailable():
    app = make_app(FakeRedis(available=False))
    response = run(endpoint(app, "/ready")())
    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
