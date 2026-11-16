"""FastAPI gateway application."""

import logging
import hashlib
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from redis.asyncio import Redis, from_url
from redis.exceptions import RedisError

from app.config import Settings
from app.metrics import ACTIVE_CLIENTS, LATENCY, RATE_LIMITED, REDIS_FAILURES, REQUESTS, prometheus_response
from app.redis_token_bucket import RedisTokenBucketLimiter

logger = logging.getLogger("rate_limiter_gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(settings: Optional[Settings] = None, redis_client: Optional[Redis] = None) -> FastAPI:
    config = settings or Settings.from_env()
    owns_redis_client = redis_client is None
    if redis_client is None:
        redis_client = from_url(
            config.redis_url,
            decode_responses=False,
            socket_connect_timeout=config.redis_connect_timeout,
            socket_timeout=config.redis_socket_timeout,
            health_check_interval=30,
            max_connections=100,
        )
    limiters = {tier: RedisTokenBucketLimiter(redis_client, limit.capacity, limit.refill_rate)
                for tier, limit in config.tiers.items()}
    counters = {"total_requests": 0, "allowed": 0, "blocked": 0, "by_tier": {}}
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if owns_redis_client:
            await redis_client.aclose()

    app = FastAPI(title="Distributed Rate Limiter Gateway", version="1.0.0", lifespan=lifespan)
    app.state.settings = config
    app.state.redis = redis_client
    app.state.limiters = limiters
    app.state.counters = counters

    app.add_middleware(CORSMiddleware, allow_origins=list(config.cors_origins),
                       allow_methods=["GET"], allow_headers=["X-API-Key", "X-Request-ID", "Content-Type"])

    @app.middleware("http")
    async def request_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        tier = "anonymous"
        try:
            if request.url.path.startswith("/api/"):
                api_key = request.headers.get("X-API-Key")
                tier = config.api_keys.get(api_key, "anonymous") if api_key else "anonymous"
                if not api_key or tier == "anonymous":
                    response = JSONResponse(status_code=401, content={"error": "Missing or invalid API key", "request_id": request_id})
                else:
                    client_id = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:32]
                    allowed, meta = await limiters[tier].allow_request(client_id)
                    counters["total_requests"] += 1
                    tier_stats = counters["by_tier"].setdefault(tier, {"allowed": 0, "blocked": 0})
                    if not allowed:
                        counters["blocked"] += 1
                        tier_stats["blocked"] += 1
                        RATE_LIMITED.labels(tier=tier).inc()
                        retry = max(1, int(meta["retry_after_seconds"] + 0.999))
                        response = JSONResponse(status_code=429, content={"error": "Rate limit exceeded", "retry_after_seconds": meta["retry_after_seconds"], "request_id": request_id}, headers={"Retry-After": str(retry)})
                        response.headers.update(_rate_headers(meta))
                    else:
                        counters["allowed"] += 1
                        tier_stats["allowed"] += 1
                        ACTIVE_CLIENTS.set(len(config.api_keys))
                        response = await call_next(request)
                        response.headers.update(_rate_headers(meta))
            else:
                response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            REQUESTS.labels(request.url.path, request.method, str(response.status_code), tier).inc()
            return response
        except (RedisError, TimeoutError) as exc:
            REDIS_FAILURES.inc()
            logger.warning("Redis rate-limit operation failed request_id=%s error=%s", request_id, type(exc).__name__)
            if config.redis_failure_mode == "open" and request.url.path.startswith("/api/"):
                response = await call_next(request)
                response.headers["X-RateLimit-Failure-Mode"] = "open"
            else:
                response = JSONResponse(status_code=503, content={"error": "Rate limiting service unavailable", "request_id": request_id})
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            LATENCY.labels(request.url.path).observe(time.perf_counter() - started)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def dashboard():
        dashboard_path = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
        return FileResponse(dashboard_path, media_type="text/html")

    @app.get("/ready")
    async def ready():
        try:
            await redis_client.ping()
        except (RedisError, TimeoutError):
            return JSONResponse(status_code=503, content={"status": "not_ready", "redis": "unavailable"})
        return {"status": "ready", "redis": "ok"}

    @app.get("/metrics")
    async def metrics():
        return prometheus_response()

    @app.get("/stats")
    async def stats():
        return counters

    @app.get("/api/ping")
    async def ping():
        return {"message": "pong", "timestamp": time.time()}

    @app.get("/api/data")
    async def data():
        return {"data": [1, 2, 3, 4, 5], "note": "This route is rate-limited"}

    @app.get("/api/status")
    async def status():
        return {"service": "rate-limiter-gateway", "tiers": list(config.tiers)}

    @app.get("/api/limits")
    async def limits(request: Request):
        tier = config.api_keys[request.headers["X-API-Key"]]
        limit = config.tiers[tier]
        return {"tier": tier, "capacity": limit.capacity, "refill_rate": limit.refill_rate}

    return app


def _rate_headers(meta: dict) -> dict:
    reset = int(time.time() + meta["retry_after_seconds"])
    return {"X-RateLimit-Limit": str(meta["capacity"]),
            "X-RateLimit-Remaining": str(meta["remaining_tokens"]),
            "X-RateLimit-Reset": str(reset)}


app = create_app()
