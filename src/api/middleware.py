import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.observability.logging_config import get_logger
from src.api.metrics import REQUEST_COUNT, REQUEST_LATENCY_SECONDS

logger = get_logger("api.requests")


def parse_traceparent(value: str | None) -> str | None:
    """Trace id from a W3C `traceparent` header (version-traceid-spanid-flags); None if absent or malformed."""
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) == 4 and len(parts[1]) == 32 and all(c in "0123456789abcdef" for c in parts[1]) and set(parts[1]) != {"0"}:
        return parts[1]
    return None


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request: path, status, latency. Assigns a request_id so a single
    request's log line can be correlated with whatever else it touches (a future
    addition could thread this into the analytics/DB layer too). Also records the
    same latency/status into the Prometheus counters in src/api/metrics.py -- one
    instrumentation point feeding both the structured log (for grepping a specific
    request) and the /metrics scrape target (for aggregate monitoring), rather than
    two separate places that could drift out of sync."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        # W3C trace context: keep the caller's trace id so one correlation id spans console -> gateway -> agent -> API.
        trace_id = parse_traceparent(request.headers.get("traceparent"))
        start = time.monotonic()

        response = await call_next(request)

        duration_s = time.monotonic() - start
        duration_ms = round(duration_s * 1000, 1)
        logger.info(
            "request handled",
            extra={
                "request_id": request_id,
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        # request.url.path for a path like /orders/2000000000_00001/risk would
        # create a distinct Prometheus label per case_id, blowing up label
        # cardinality over time -- use the matched ROUTE template instead
        # (e.g. /orders/{case_id}/risk), which FastAPI exposes once routing
        # has resolved the request.
        route = request.scope.get("route")
        path_label = route.path if route is not None else request.url.path
        REQUEST_COUNT.labels(method=request.method, path=path_label,
                              status_code=str(response.status_code)).inc()
        REQUEST_LATENCY_SECONDS.labels(method=request.method, path=path_label).observe(duration_s)

        response.headers["X-Request-ID"] = request_id
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id
        return response
