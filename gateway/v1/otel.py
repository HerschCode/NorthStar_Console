"""OpenTelemetry spans for the pre/post-flight layers of one gateway request.

Uses only the OpenTelemetry *API*: with no SDK / exporter configured by the operator (OTEL_* env, a collector) every call is a
no-op, so the gateway stays dependency-light. The spans are emitted after the request from the per-layer trace the middleware
already produces, so their wall-clock durations are ~0; the measured layer latency is carried in the `latency_ms` attribute.
The trace id is the one propagated from the caller's `traceparent`, so these spans join the console / P2 / P1 trace.
"""
from __future__ import annotations

import secrets


def emit_spans(trace_id: str, route: str, per_layer: dict, decision: str, total_ms: float) -> bool:
    try:
        from opentelemetry import trace
        from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context
    except ImportError:
        return False
    try:
        parent = SpanContext(trace_id=int(trace_id, 16), span_id=int(secrets.token_hex(8), 16), is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED))
    except ValueError:
        return False
    tracer = trace.get_tracer("northstar.gateway")
    with tracer.start_as_current_span(f"gateway.{route}", context=set_span_in_context(NonRecordingSpan(parent)),
                                      attributes={"gateway.route": route, "gateway.decision": decision, "gateway.total_ms": round(total_ms, 2)}):
        for layer, info in (per_layer or {}).items():
            with tracer.start_as_current_span(f"gateway.layer.{layer}", attributes={
                    "gateway.layer": layer, "gateway.blocked": bool(info.get("blocked")), "gateway.skipped": str(info.get("skipped") or ""),
                    "gateway.latency_ms": round(float(info.get("latency_ms", 0) or 0), 3)}):
                pass
    return True
