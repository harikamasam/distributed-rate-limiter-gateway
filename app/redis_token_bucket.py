"""Atomic Redis-backed token bucket limiter."""

import time
from typing import Optional, Tuple

from redis.asyncio import Redis


TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'last_refill')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])
if tokens == nil then
  tokens = capacity
  last_refill = now
end
local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'last_refill', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / refill_rate) + 5)
return {allowed, tokens}
"""


class RedisTokenBucketLimiter:
    """Distributed limiter; Lua makes read/refill/consume/write atomic."""

    def __init__(self, redis_client: Redis, capacity: int, refill_rate: float,
                 key_prefix: str = "ratelimit") -> None:
        if capacity <= 0 or refill_rate <= 0:
            raise ValueError("capacity and refill_rate must be positive")
        self.redis = redis_client
        self.capacity = capacity
        self.refill_rate = float(refill_rate)
        self.key_prefix = key_prefix.rstrip(":")
        self._script = self.redis.register_script(TOKEN_BUCKET_LUA)

    async def allow_request(self, client_id: str,
                            now: Optional[float] = None) -> Tuple[bool, dict]:
        if not client_id:
            raise ValueError("client_id must not be empty")
        result = await self._script(
            keys=[f"{self.key_prefix}:{client_id}"],
            args=[self.capacity, self.refill_rate, now or time.time()],
        )
        allowed = bool(int(result[0]))
        remaining = max(0.0, float(result[1]))
        retry = max(0.0, (1.0 - remaining) / self.refill_rate)
        return allowed, {"remaining_tokens": round(remaining, 3),
                         "capacity": self.capacity,
                         "retry_after_seconds": round(retry, 3)}
