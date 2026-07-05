"""
Unit tests for TokenBucketLimiter.

Run with: pytest tests/test_token_bucket.py -v
"""
import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.token_bucket import TokenBucketLimiter


def test_allows_requests_within_capacity():
    limiter = TokenBucketLimiter(capacity=5, refill_rate=1)
    for i in range(5):
        allowed, meta = limiter.allow_request("client_a")
        assert allowed is True, f"Request {i+1} should be allowed (within capacity)"


def test_rejects_requests_beyond_capacity():
    limiter = TokenBucketLimiter(capacity=3, refill_rate=1)
    for _ in range(3):
        limiter.allow_request("client_b")
    # 4th request immediately after should be rejected (no refill time passed)
    allowed, meta = limiter.allow_request("client_b")
    assert allowed is False
    assert meta["remaining_tokens"] == 0


def test_tokens_refill_over_time():
    limiter = TokenBucketLimiter(capacity=2, refill_rate=2)  # 2 tokens/sec
    limiter.allow_request("client_c")
    limiter.allow_request("client_c")
    # bucket now empty
    allowed, _ = limiter.allow_request("client_c")
    assert allowed is False

    time.sleep(0.6)  # ~1.2 tokens should have refilled
    allowed, meta = limiter.allow_request("client_c")
    assert allowed is True, "Token should have refilled after waiting"


def test_clients_are_isolated():
    """Client A being rate-limited should NOT affect Client B."""
    limiter = TokenBucketLimiter(capacity=1, refill_rate=1)
    limiter.allow_request("client_x")
    allowed_x, _ = limiter.allow_request("client_x")
    assert allowed_x is False  # client_x is out of tokens

    allowed_y, _ = limiter.allow_request("client_y")
    assert allowed_y is True  # client_y has its own bucket


def test_burst_then_sustained_rate():
    """
    This is the key property that differentiates token bucket from a
    naive fixed-window counter: allow a burst up to capacity, then
    settle into the refill rate.
    """
    limiter = TokenBucketLimiter(capacity=5, refill_rate=1)  # 1 token/sec after burst
    results = [limiter.allow_request("client_z")[0] for _ in range(5)]
    assert all(results), "Initial burst of 5 (== capacity) should all succeed"

    # Immediately after burst, bucket is empty -> next request fails
    allowed, _ = limiter.allow_request("client_z")
    assert allowed is False

    # After waiting 1 second, refill_rate=1 means exactly 1 new token
    time.sleep(1.05)
    allowed, _ = limiter.allow_request("client_z")
    assert allowed is True


def test_retry_after_is_reasonable():
    limiter = TokenBucketLimiter(capacity=1, refill_rate=0.5)  # 1 token per 2 sec
    limiter.allow_request("client_r")
    allowed, meta = limiter.allow_request("client_r")
    assert allowed is False
    # Should need to wait close to 2 seconds for the next token
    assert 1.5 <= meta["retry_after_seconds"] <= 2.5


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
