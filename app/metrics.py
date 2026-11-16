"""Prometheus metrics with bounded labels."""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

REQUESTS = Counter("gateway_requests_total", "Gateway requests", ["route", "method", "status", "tier"])
RATE_LIMITED = Counter("gateway_rate_limited_total", "Rejected requests", ["tier"])
LATENCY = Histogram("gateway_request_latency_seconds", "Request latency", ["route"])
REDIS_FAILURES = Counter("gateway_redis_failures_total", "Redis failures")
ACTIVE_CLIENTS = Gauge("gateway_active_clients", "Known clients in this process")


def prometheus_response():
    from fastapi.responses import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
