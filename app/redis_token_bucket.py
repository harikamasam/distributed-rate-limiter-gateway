"""
Distributed Token Bucket - Redis-backed Implementation

Why Redis (interview talking point):
    The in-memory version (Phase 1) breaks the moment you run more than
    one instance of your API server (which any real production API does,
    for scale + failover). Each server would have its OWN dict of buckets,
    so a client could get 5x the intended rate by hitting 5 different
    server instances.

    Redis gives all server instances a SHARED, single source of truth for
    bucket state, so the rate limit is enforced correctly no matter which
    server handles the request.

Why a Lua script (the hard part / best interview talking point):
    A naive approach does this in Python:
        tokens = redis.get(key)          # READ
        tokens = refill(tokens)          # COMPUTE
        redis.set(key, tokens - 1)       # WRITE
    This has a race condition: if two requests from the same client hit
    two different server processes at the same time, both could READ the
    same token count before either WRITEs, and both get allowed even
    though only 1 token was available. This is a classic
    check-then-act race condition.

    Redis executes Lua scripts ATOMICALLY (single-threaded, no other
    command runs in the middle). By doing the read-compute-write inside
    one Lua script, we get correctness guarantees identical to a mutex
    lock, but without needing a distributed lock (which would be slower
    and more complex).

Complexity: O(1) per request (Redis operations are O(1), Lua script is
short and has no loops over unbounded data).
"""

import time
import redis


# Lua script executed atomically inside Redis.
# KEYS[1] = bucket key (e.g. "ratelimit:{client_id}")
# ARGV[1] = capacity
# ARGV[2] = refill_rate (tokens per second)
# ARGV[3] = current timestamp (epoch seconds, passed in from Python so
#           all servers agree on "now" even if their system clocks drift
#           slightly -- in production you'd sync via NTP, but passing it
#           explicitly makes the script deterministic and testable)
TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local bucket = redis.call("HMGET", key, "tokens", "last_refill")
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

if tokens == nil then
    -- first time we've seen this client: start with a full bucket
    tokens = capacity
    last_refill = now
end

-- refill based on elapsed time since last request
local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + (elapsed * refill_rate))

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

redis.call("HMSET", key, "tokens", tokens, "last_refill", now)
-- expire the key if the client goes idle for long enough to fully
-- refill anyway, so we don't leak memory for one-off/abandoned clients
local ttl = math.ceil(capacity / refill_rate) + 5
redis.call("EXPIRE", key, ttl)

return {allowed, tostring(tokens)}
"""


class RedisTokenBucketLimiter:
    def __init__(self, redis_client: redis.Redis, capacity: int, refill_rate: float):
        self.redis = redis_client
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._script = self.redis.register_script(TOKEN_BUCKET_LUA)

    def allow_request(self, client_id: str) -> tuple[bool, dict]:
        key = f"ratelimit:{client_id}"
        now = time.time()

        allowed_flag, remaining_str = self._script(
            keys=[key],
            args=[self.capacity, self.refill_rate, now],
        )

        allowed = bool(int(allowed_flag))
        remaining = float(remaining_str)

        if remaining >= 1:
            retry_after = 0.0
        else:
            retry_after = (1 - remaining) / self.refill_rate

        return allowed, {
            "remaining_tokens": round(remaining, 2),
            "capacity": self.capacity,
            "retry_after_seconds": round(retry_after, 2),
        }
