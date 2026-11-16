"""Thread-safe, process-local token bucket implementation."""

from dataclasses import dataclass
import threading
import time
from typing import Callable, Dict, Optional, Tuple


@dataclass
class BucketState:
    tokens: float
    last_refill: float


class TokenBucketLimiter:
    """A reference limiter for one process.

    State is local to this process; use the Redis implementation for multiple
    gateway instances. ``clock`` is injectable for deterministic tests.
    """

    def __init__(self, capacity: int, refill_rate: float,
                 clock: Optional[Callable[[], float]] = None) -> None:
        if capacity <= 0 or refill_rate <= 0:
            raise ValueError("capacity and refill_rate must be positive")
        self.capacity = capacity
        self.refill_rate = float(refill_rate)
        self._clock = clock or time.monotonic
        self._buckets: Dict[str, BucketState] = {}
        self._lock = threading.Lock()

    def allow_request(self, client_id: str) -> Tuple[bool, dict]:
        if not client_id:
            raise ValueError("client_id must not be empty")
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(client_id)
            if bucket is None:
                bucket = BucketState(float(self.capacity), now)
                self._buckets[client_id] = bucket
            else:
                elapsed = max(0.0, now - bucket.last_refill)
                bucket.tokens = min(self.capacity,
                                    bucket.tokens + elapsed * self.refill_rate)
                bucket.last_refill = now
            allowed = bucket.tokens >= 1.0
            if allowed:
                bucket.tokens -= 1.0
            remaining = max(0.0, bucket.tokens)
            return allowed, self._metadata(remaining)

    def _metadata(self, remaining: float) -> dict:
        retry = max(0.0, (1.0 - remaining) / self.refill_rate)
        return {"remaining_tokens": round(remaining, 3),
                "capacity": self.capacity,
                "retry_after_seconds": round(retry, 3)}

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()
