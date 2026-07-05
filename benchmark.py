"""
Load Test / Benchmark Script

Purpose: prove the rate limiter behaves correctly AND measure the
overhead it adds, so you have REAL numbers for your resume instead of
vague claims like "improved performance."

What this measures:
    1. Correctness under concurrent load: does a client with capacity=5
       actually get exactly 5 allowed + the rest blocked, even when 50
       requests fire at once from multiple concurrent workers?
    2. Latency overhead: how many extra milliseconds does the rate-limit
       check add per request (Redis round-trip cost)?
    3. Throughput: how many requests/second can the gateway handle?

Run this AFTER starting the server:
    uvicorn app.main:app --reload
    python benchmark.py
"""

import asyncio
import time
import httpx

BASE_URL = "http://localhost:8000"
FREE_KEY = "free-key-123"      # capacity=5, refill_rate=1
PREMIUM_KEY = "premium-key-456"  # capacity=50, refill_rate=10


async def fire_requests(client: httpx.AsyncClient, api_key: str, n: int):
    """Fire n concurrent requests and return (allowed_count, blocked_count, latencies_ms)."""
    async def one_request():
        start = time.perf_counter()
        resp = await client.get(
            f"{BASE_URL}/api/ping",
            headers={"X-API-Key": api_key},
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        return resp.status_code, elapsed_ms

    results = await asyncio.gather(*[one_request() for _ in range(n)])
    allowed = sum(1 for status, _ in results if status == 200)
    blocked = sum(1 for status, _ in results if status == 429)
    latencies = [lat for _, lat in results]
    return allowed, blocked, latencies


async def test_correctness_under_burst():
    print("\n=== TEST 1: Correctness under concurrent burst ===")
    async with httpx.AsyncClient() as client:
        # free tier has capacity=5 -> firing 50 at once should allow
        # exactly 5 and block the other 45, even though they're concurrent
        allowed, blocked, _ = await fire_requests(client, FREE_KEY, 50)
        print(f"Fired 50 concurrent requests on free tier (capacity=5)")
        print(f"  Allowed: {allowed} (expected: 5)")
        print(f"  Blocked: {blocked} (expected: 45)")
        status = "PASS" if allowed == 5 else "FAIL"
        print(f"  Result: {status}")


async def test_latency_overhead():
    print("\n=== TEST 2: Latency overhead added by rate limiter ===")
    async with httpx.AsyncClient() as client:
        # premium tier has high capacity, so most of these succeed and
        # we're measuring the Redis round-trip cost, not blocking behavior
        allowed, blocked, latencies = await fire_requests(client, PREMIUM_KEY, 100)
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        avg = sum(latencies) / len(latencies)
        print(f"100 requests on premium tier (capacity=50, refill=10/sec)")
        print(f"  Allowed: {allowed}, Blocked: {blocked}")
        print(f"  Latency avg: {avg:.2f}ms | p50: {p50:.2f}ms | p95: {p95:.2f}ms | p99: {p99:.2f}ms")


async def test_sustained_throughput():
    print("\n=== TEST 3: Sustained throughput over 5 seconds ===")
    async with httpx.AsyncClient() as client:
        start = time.perf_counter()
        total_allowed = 0
        total_blocked = 0
        while time.perf_counter() - start < 5:
            allowed, blocked, _ = await fire_requests(client, PREMIUM_KEY, 20)
            total_allowed += allowed
            total_blocked += blocked
            await asyncio.sleep(0.1)
        elapsed = time.perf_counter() - start
        rps = total_allowed / elapsed
        print(f"Ran for {elapsed:.1f}s")
        print(f"  Total allowed: {total_allowed}, blocked: {total_blocked}")
        print(f"  Sustained throughput: {rps:.1f} allowed req/sec")


async def main():
    print("Rate Limiter Gateway - Benchmark Suite")
    print("Make sure the server is running: uvicorn app.main:app")
    await test_correctness_under_burst()
    await asyncio.sleep(6)  # let free tier bucket fully refill before next test
    await test_latency_overhead()
    await test_sustained_throughput()
    print("\nDone. Use these numbers in your resume/README (see the numbers")
    print("above for allowed/blocked accuracy, p95 latency overhead, and")
    print("sustained req/sec -- do NOT invent numbers, always report what")
    print("this script actually measured on your machine.")


if __name__ == "__main__":
    asyncio.run(main())
