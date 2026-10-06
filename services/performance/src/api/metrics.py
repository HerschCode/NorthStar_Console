"""
Prometheus instrumentation. `RequestLoggingMiddleware` already logged
per-request latency to structured JSON logs (real, but only queryable by
grepping log lines) -- this exposes the same signal, plus prediction counts,
in the format an actual monitoring stack (Prometheus + Grafana, the
combination this project's own docs named as a gap) can scrape directly.

Deliberately uses the standard `prometheus_client` library rather than
hand-rolling the text exposition format -- the point of this endpoint is to
be a real scrape target for real tooling, not a lookalike.
"""
from prometheus_client import Counter, Histogram, CONTENT_TYPE_LATEST, generate_latest

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests handled",
    ["method", "path", "status_code"],
)

REQUEST_LATENCY_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    # Tuned to this API's actual observed range (docs/api-latency.md: several
    # seconds per request, dominated by full-table reads with no caching) --
    # default prometheus_client buckets top out at 10s, too coarse here.
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 7.5, 10, 15, 30, float("inf")),
)

PREDICTIONS_TOTAL = Counter(
    "sla_predictions_total",
    "Total SLA-risk predictions served by /orders/{case_id}/risk",
    ["risk_level"],
)


def render_metrics() -> tuple[bytes, str]:
    """Returns (body, content_type) -- what a FastAPI route handler needs to
    return a valid Prometheus scrape response."""
    return generate_latest(), CONTENT_TYPE_LATEST
