"""
Token Bucket Rate Limiting Algorithm - In-Memory Implementation

Concept:
    - Each client gets a "bucket" holding up to `capacity` tokens.
    - Tokens refill continuously at `refill_rate` tokens per second.
    - Each request consumes 1 token.
    - If no tokens are available, the request is rejected.

Why this algorithm (interview talking point):
    - Allows short bursts (up to `capacity` requests at once) while still
      enforcing a long-term average rate (`refill_rate` per second).
    - Compare to a naive "fixed window counter" (e.g. max 100 req/minute):
      that approach allows 2x the limit right at a window boundary
      (100 requests at 0:59, another 100 at 1:01 = 200 in 2 seconds).
      Token bucket avoids this because tokens refill continuously, not
      in discrete resets.

Complexity:
    - O(1) time per request check.
    - O(1) space per client (just two numbers: current tokens, last refill time).
"""

import time
import threading
from dataclasses import dataclass


@dataclass
class BucketState:
    tokens: float          # current number of tokens available
    last_refill: float     # last time (epoch seconds) we topped up tokens


class TokenBucketLimiter:
    def __init__(self, capacity: int, refill_rate: float):
        """
        capacity: max tokens the bucket can hold (max burst size)
        refill_rate: tokens added per second (sustained allowed rate)
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._buckets: dict[str, BucketState] = {}
        # A lock is needed because multiple requests can hit this
        # concurrently (FastAPI runs async, but under load with threads/
        # multiple workers, dict mutation isn't atomic). This is an
        # in-memory-only solution — Phase 2 replaces this with Redis
        # so it works correctly across multiple server processes/machines.
        self._lock = threading.Lock()

    def _refill(self, bucket: BucketState, now: float) -> None:
        elapsed = now - bucket.last_refill
        if elapsed <= 0:
            return
        refill_amount = elapsed * self.refill_rate
        bucket.tokens = min(self.capacity, bucket.tokens + refill_amount)
        bucket.last_refill = now

    def allow_request(self, client_id: str) -> tuple[bool, dict]:
        """
        Returns (allowed: bool, meta: dict) where meta contains
        remaining tokens and seconds until next token is available
        (useful for building the Retry-After header later).
        """
        now = time.monotonic()

        with self._lock:
            bucket = self._buckets.get(client_id)
            if bucket is None:
                # New client starts with a full bucket
                bucket = BucketState(tokens=self.capacity, last_refill=now)
                self._buckets[client_id] = bucket
            else:
                self._refill(bucket, now)

            if bucket.tokens >= 1:
                bucket.tokens -= 1
                allowed = True
            else:
                allowed = False

            remaining = bucket.tokens
            # Time until at least 1 token is available again
            if remaining >= 1:
                retry_after = 0.0
            else:
                tokens_needed = 1 - remaining
                retry_after = tokens_needed / self.refill_rate

            return allowed, {
                "remaining_tokens": round(remaining, 2),
                "capacity": self.capacity,
                "retry_after_seconds": round(retry_after, 2),
            }
