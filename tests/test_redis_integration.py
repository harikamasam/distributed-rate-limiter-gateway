"""Real Redis tests. Run with a disposable Redis on localhost:6379."""

import asyncio
import socket

import httpx
import pytest
from redis.asyncio import Redis

from app.config import Settings, TierLimit
from app.main import create_app
from app.redis_token_bucket import RedisTokenBucketLimiter


REDIS_URL = "redis://127.0.0.1:6379/15"
try:
    _probe = socket.create_connection(("127.0.0.1", 6379), timeout=0.2)
    _probe.close()
    HAS_REDIS = True
except OSError:
    HAS_REDIS = False

pytestmark = pytest.mark.skipif(not HAS_REDIS, reason="Redis is not running on localhost:6379")


@pytest.fixture
async def redis_client():
    client = Redis.from_url(REDIS_URL, decode_responses=False,
                            socket_connect_timeout=0.2, socket_timeout=0.2)
    try:
        await client.ping()
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"Redis integration tests require localhost Redis: {exc}")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.mark.asyncio
async def test_atomic_consumption_and_refill(redis_client):
    limiter = RedisTokenBucketLimiter(redis_client, 3, 10, "test")
    results = await asyncio.gather(*(limiter.allow_request("client", now=100) for _ in range(20)))
    assert sum(allowed for allowed, _ in results) == 3
    allowed, meta = await limiter.allow_request("client", now=100.2)
    assert allowed and meta["remaining_tokens"] == 1


@pytest.mark.asyncio
async def test_clients_are_isolated(redis_client):
    limiter = RedisTokenBucketLimiter(redis_client, 1, 1, "test")
    assert (await limiter.allow_request("a", now=100))[0]
    assert not (await limiter.allow_request("a", now=100))[0]
    assert (await limiter.allow_request("b", now=100))[0]


@pytest.mark.asyncio
async def test_two_app_instances_share_redis_state(redis_client):
    settings = Settings(REDIS_URL, 1, 1, "closed", {"test-key": "free"},
                        {"free": TierLimit(2, 1)}, ("http://testserver",))
    app_one = create_app(settings)
    app_two = create_app(settings)
    await redis_client.flushdb()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_one), base_url="http://testserver") as client_one, \
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app_two), base_url="http://testserver") as client_two:
        responses = await asyncio.gather(
            client_one.get("/api/ping", headers={"X-API-Key": "test-key"}),
            client_two.get("/api/ping", headers={"X-API-Key": "test-key"}),
            client_one.get("/api/ping", headers={"X-API-Key": "test-key"}),
        )
    assert [response.status_code for response in responses].count(200) == 2
    assert [response.status_code for response in responses].count(429) == 1
    await app_one.state.redis.aclose()
    await app_two.state.redis.aclose()


@pytest.mark.asyncio
async def test_api_auth_headers_and_readiness(redis_client):
    settings = Settings(REDIS_URL, 1, 1, "closed", {"test-key": "free"},
                        {"free": TierLimit(1, 1)}, ("http://testserver",))
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/api/ping")).status_code == 401
        first = await client.get("/api/ping", headers={"X-API-Key": "test-key"})
        second = await client.get("/api/ping", headers={"X-API-Key": "test-key"})
        assert first.status_code == 200
        assert second.status_code == 429
        assert second.headers["Retry-After"]
        assert second.headers["X-RateLimit-Limit"] == "1"
        assert "X-Request-ID" in first.headers
        assert (await client.get("/ready")).status_code == 200
    await app.state.redis.aclose()
