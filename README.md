# Distributed Rate Limiter & API Gateway

A production-style API gateway that enforces per-client rate limits using the
**Token Bucket** algorithm, backed by **Redis** for correctness across multiple
server instances. Built to demonstrate system design and algorithmic
decision-making, not just CRUD/auth wiring.

## Why this project exists

Most portfolio projects are "React frontend + FastAPI backend + JWT auth."
This one is different on purpose: it tackles a real **system design interview
question** (rate limiting is asked constantly at Google/Microsoft/Amazon) and
implements it correctly, including the distributed-systems edge case that
naive solutions miss: **race conditions across multiple server instances.**

## Architecture

```
                     ┌─────────────┐
   Client Request →  │  FastAPI    │
                     │  Gateway    │
                     │ (middleware)│
                     └──────┬──────┘
                            │  atomic check via Lua script
                            ▼
                     ┌─────────────┐
                     │    Redis    │  ← shared state across
                     │  (bucket    │    ALL gateway instances
                     │   state)    │
                     └─────────────┘
```

Multiple instances of the FastAPI gateway can run behind a load balancer.
Because bucket state lives in Redis (not in each process's memory), the rate
limit is enforced correctly no matter which instance handles a given
request — this is the core "distributed" property of the system.

## The algorithm: Token Bucket

- Each client has a bucket holding up to `capacity` tokens.
- Tokens refill continuously at `refill_rate` tokens/second.
- Each request costs 1 token; no tokens = request rejected (`429`).

**Why not a simpler fixed-window counter (e.g. "max 100/minute")?**
A fixed window resets abruptly at each boundary, so a client can send 100
requests at `0:59` and another 100 at `1:01` — 200 requests in 2 real seconds,
double the intended rate. Token bucket refills continuously, so this
boundary-burst problem doesn't happen, while still allowing legitimate short
bursts up to `capacity`.

**Why Redis + a Lua script instead of plain Redis GET/SET?**
A naive read-modify-write (`GET` tokens, compute, `SET` tokens) has a race
condition: two concurrent requests from the same client can both read the
same token count before either writes back, both get allowed, and the limit
is silently violated. Redis executes Lua scripts atomically (no other
command interleaves), so the read-compute-write happens as one indivisible
operation — giving correctness without needing a separate distributed lock.

## Project structure

```
rate-limiter-gateway/
├── app/
│   ├── token_bucket.py         # Phase 1: in-memory algorithm + tests
│   ├── redis_token_bucket.py   # Phase 2: distributed version (Lua script)
│   └── main.py                 # Phase 3: FastAPI gateway + middleware
├── tests/
│   └── test_token_bucket.py    # Unit tests, edge cases
├── dashboard/
│   └── index.html              # Phase 5: live stats dashboard
├── benchmark.py                 # Phase 4: load test / performance numbers
└── requirements.txt
```

## Running it locally

1. **Start Redis** (needs Docker, or install Redis directly):
   ```bash
   docker run -p 6379:6379 redis:7
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the unit tests:**
   ```bash
   python -m pytest tests/ -v
   ```

4. **Start the gateway:**
   ```bash
   uvicorn app.main:app --reload
   ```

5. **Open the dashboard:** just open `dashboard/index.html` in a browser
   (no build step needed).

6. **Try it manually:**
   ```bash
   curl -H "X-API-Key: free-key-123" http://localhost:8000/api/ping
   # repeat 6+ times quickly -> the 6th returns 429 (free tier capacity=5)
   ```

7. **Run the benchmark suite** (produces real performance numbers):
   ```bash
   python benchmark.py
   ```

## What the benchmark measures

- **Correctness under concurrency:** fires 50 simultaneous requests against
  a client with capacity=5, verifies exactly 5 are allowed even though
  they're concurrent (proves the Lua script actually prevents race
  conditions — this was hard to get right and is the reason not to just
  use plain Redis GET/SET).
- **Latency overhead:** p50/p95/p99 latency added by the rate-limit check
  itself (the Redis round-trip cost).
- **Sustained throughput:** allowed requests/sec over a 5-second window.

Run `benchmark.py` yourself and record the actual numbers your machine
produces — use those (not invented numbers) in your resume/interview.

## Possible extensions (if you want to go further)

- Add a **sliding window log** algorithm as a second strategy and compare
  memory/accuracy tradeoffs against token bucket.
- Add Prometheus metrics + a Grafana dashboard instead of the simple HTML one.
- Deploy behind an actual load balancer (2+ gateway instances) to visually
  demonstrate the distributed correctness property.
