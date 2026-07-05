"""
API Gateway with Rate Limiting Middleware

This is the "product" layer: a FastAPI app that sits in front of demo
backend routes and enforces per-client rate limits using the Redis
token bucket from Phase 2.

Real-world pattern this demonstrates:
    Products like Stripe/GitHub give different rate limits per client
    tier (free vs paid). We simulate that with a simple API-key -> tier
    lookup. In a real system this would come from a database/auth
    service; here it's an in-memory dict for demo purposes.
"""

import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import redis

from app.redis_token_bucket import RedisTokenBucketLimiter

app = FastAPI(title="Rate-Limited API Gateway")

# Allow the standalone dashboard/index.html (opened directly in a browser)
# to call this API. In production you'd restrict this to your real
# frontend's origin instead of "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Redis connection ---
# In production this would point at a managed Redis cluster (e.g. Azure
# Cache for Redis / AWS ElastiCache) shared by every server instance.
redis_client = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

# --- Per-tier rate limit config ---
# (capacity = max burst, refill_rate = tokens/sec = sustained rate)
TIER_LIMITS = {
    "free": {"capacity": 5, "refill_rate": 1},         # 1 req/sec, burst of 5
    "premium": {"capacity": 50, "refill_rate": 10},    # 10 req/sec, burst of 50
}

# Demo API-key -> tier mapping (would be a DB lookup in production)
API_KEYS = {
    "free-key-123": "free",
    "premium-key-456": "premium",
}

_limiters: dict[str, RedisTokenBucketLimiter] = {
    tier: RedisTokenBucketLimiter(redis_client, cfg["capacity"], cfg["refill_rate"])
    for tier, cfg in TIER_LIMITS.items()
}

# In-memory stats for the dashboard (Phase 5). In production you'd push
# these to a metrics system (Prometheus, CloudWatch, etc.) instead.
stats = {
    "total_requests": 0,
    "allowed": 0,
    "blocked": 0,
    "by_client": {},
}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Only guard routes under /api/ -- dashboard/stats endpoints stay open
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    api_key = request.headers.get("X-API-Key")
    if api_key is None or api_key not in API_KEYS:
        return JSONResponse(
            status_code=401,
            content={"error": "Missing or invalid X-API-Key header"},
        )

    tier = API_KEYS[api_key]
    limiter = _limiters[tier]
    allowed, meta = limiter.allow_request(client_id=api_key)

    # --- update stats for dashboard ---
    stats["total_requests"] += 1
    client_stats = stats["by_client"].setdefault(
        api_key, {"allowed": 0, "blocked": 0, "tier": tier}
    )
    if allowed:
        stats["allowed"] += 1
        client_stats["allowed"] += 1
    else:
        stats["blocked"] += 1
        client_stats["blocked"] += 1

    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "retry_after_seconds": meta["retry_after_seconds"],
            },
            headers={"Retry-After": str(int(meta["retry_after_seconds"]) + 1)},
        )

    response = await call_next(request)
    # Expose remaining quota to the client, like real APIs do
    response.headers["X-RateLimit-Remaining"] = str(meta["remaining_tokens"])
    response.headers["X-RateLimit-Capacity"] = str(meta["capacity"])
    return response


# --- Demo protected routes (stand-ins for a "real" backend) ---

@app.get("/api/ping")
async def ping():
    return {"message": "pong", "timestamp": time.time()}


@app.get("/api/data")
async def get_data():
    return {"data": [1, 2, 3, 4, 5], "note": "This route is rate-limited"}


# --- Dashboard/stats endpoints (NOT rate-limited, used by Phase 5) ---

@app.get("/stats")
async def get_stats():
    return stats


@app.get("/health")
async def health():
    try:
        redis_client.ping()
        redis_ok = True
    except redis.exceptions.ConnectionError:
        redis_ok = False
    return {"status": "ok" if redis_ok else "degraded", "redis_connected": redis_ok}
