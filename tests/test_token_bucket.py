import pytest

from app.token_bucket import TokenBucketLimiter


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_capacity_and_exhaustion():
    limiter = TokenBucketLimiter(3, 1, clock=FakeClock())
    assert [limiter.allow_request("a")[0] for _ in range(3)] == [True] * 3
    allowed, meta = limiter.allow_request("a")
    assert not allowed
    assert meta["remaining_tokens"] == 0


def test_refill_is_continuous_and_capped():
    clock = FakeClock()
    limiter = TokenBucketLimiter(2, 2, clock=clock)
    limiter.allow_request("a")
    limiter.allow_request("a")
    clock.now += 0.5
    allowed, meta = limiter.allow_request("a")
    assert allowed and meta["remaining_tokens"] == 0
    clock.now += 100
    assert limiter.allow_request("a")[1]["remaining_tokens"] == 1


def test_clients_are_isolated_and_concurrent_consumption_is_safe():
    import concurrent.futures
    limiter = TokenBucketLimiter(10, 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(lambda _: limiter.allow_request("same")[0], range(30)))
    assert sum(results) == 10
    assert limiter.allow_request("other")[0]


def test_invalid_configuration_and_client_are_rejected():
    with pytest.raises(ValueError):
        TokenBucketLimiter(0, 1)
    with pytest.raises(ValueError):
        TokenBucketLimiter(1, 0)
    with pytest.raises(ValueError):
        TokenBucketLimiter(1, 1).allow_request("")
