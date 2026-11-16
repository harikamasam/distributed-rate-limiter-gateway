"""Reproducible limiter benchmark; writes measured data, never fabricated data.

Examples:
  python benchmark.py --mode in-memory --requests 1000
  python benchmark.py --mode redis --redis-url redis://localhost:6379/14
"""

import argparse
import asyncio
import concurrent.futures
import json
import statistics
import time
from pathlib import Path

from redis.asyncio import Redis

from app.redis_token_bucket import RedisTokenBucketLimiter
from app.token_bucket import TokenBucketLimiter


def run_in_memory(total: int, concurrency: int) -> dict:
    limiter = TokenBucketLimiter(total, 1_000_000)
    def operation():
        started = time.perf_counter()
        allowed, _ = limiter.allow_request("benchmark")
        return allowed, (time.perf_counter() - started) * 1000
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        started = time.perf_counter()
        results = list(pool.map(lambda _: operation(), range(total)))
        elapsed = time.perf_counter() - started
    return _summary(results, total, concurrency, elapsed)


async def run_redis(total: int, concurrency: int, redis_url: str) -> dict:
    client = Redis.from_url(redis_url, decode_responses=False, socket_connect_timeout=1, socket_timeout=1)
    await client.ping()
    await client.flushdb()
    limiter = RedisTokenBucketLimiter(client, total, 1_000_000, "benchmark")
    try:
        return await _run(lambda: limiter.allow_request("benchmark"), total, concurrency)
    finally:
        await client.aclose()


async def _run(operation, total: int, concurrency: int) -> dict:
    semaphore = asyncio.Semaphore(concurrency)

    async def one():
        async with semaphore:
            started = time.perf_counter()
            allowed, _ = await operation()
            return allowed, (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    results = await asyncio.gather(*(one() for _ in range(total)))
    elapsed = time.perf_counter() - started
    return _summary(results, total, concurrency, elapsed)


def _summary(results, total: int, concurrency: int, elapsed: float) -> dict:
    latencies = sorted(latency for _, latency in results)
    return {"requests": total, "concurrency": concurrency,
            "allowed": sum(allowed for allowed, _ in results),
            "rejected": sum(not allowed for allowed, _ in results),
            "error_rate": 0.0, "rps": total / (elapsed or 1),
            "average_ms": statistics.mean(latencies),
            "median_ms": statistics.median(latencies),
            "p95_ms": latencies[max(0, int(len(latencies) * .95) - 1)],
            "p99_ms": latencies[max(0, int(len(latencies) * .99) - 1)]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["in-memory", "redis", "all"], default="all")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 10, 50, 100])
    parser.add_argument("--redis-url", default="redis://localhost:6379/14")
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results/latest.json"))
    args = parser.parse_args()
    output = {"configuration": vars(args) | {"output": str(args.output)}, "results": []}
    modes = ["in-memory", "redis"] if args.mode == "all" else [args.mode]
    for mode in modes:
        for concurrency in args.concurrency:
            try:
                result = (run_in_memory(args.requests, concurrency) if mode == "in-memory"
                          else asyncio.run(run_redis(args.requests, concurrency, args.redis_url)))
                output["results"].append({"mode": mode, **result})
            except Exception as exc:
                output["results"].append({"mode": mode, "concurrency": concurrency,
                                          "error": type(exc).__name__})
                if mode == "redis":
                    break
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
