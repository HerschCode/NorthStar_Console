"""/v1: the gateway as the single front door for the Northstar console.

- AI routes (/v1/ask, /v1/investigations, /v1/nl-filter, /v1/briefing) run pre-flight (PII, injection ensemble, rate limits),
  call the assistant (P2) and run post-flight (leak, role exposure) before the answer reaches the console; every response
  carries a `gateway` object (decision, layers, latency) so the UI can show the security check.
- Action routes proxy proposals/approvals; the approval queue and policy are the gateway's (gateway/actions/).
- Governance routes read PERSISTED decisions (gateway/v1/store.py), never in-process counters.
- The Attack Lab runs scenarios through the real pipeline (gateway/v1/lab.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from gateway.actions.api import get_firewall
from gateway.actions.approvals import ApprovalConflict, ApprovalError, ApprovalForbidden, ApprovalNotFound
from gateway.actions.policy import DEFAULT_POLICY_PATH, Principal
from gateway.ip_limits import ip_rate_limit
from gateway.v1 import identity as ident
from gateway.v1 import lab
from gateway.v1.identity import Identity, current_identity, require_identity, require_roles
from gateway.v1.proxy import CapturingAdapter, P2Client, UpstreamError
from gateway.v1.store import WINDOWS, GovernanceStore

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix="/v1", tags=["v1"])


class Deps:
    def __init__(self):
        self.middleware = None
        self._store: GovernanceStore | None = None
        self.p2: P2Client | None = None

    @property
    def store(self) -> GovernanceStore:
        if self._store is None:
            self._store = GovernanceStore()
        return self._store

    @store.setter
    def store(self, v):
        self._store = v

    def p2_client(self) -> P2Client:
        return self.p2 or P2Client()


deps = Deps()


# ── trace context ──
def trace_context(request: Request) -> tuple[str, str]:
    """(trace_id, traceparent to forward). Generated here when the caller sent none."""
    cached = getattr(request.state, "trace_ctx", None)
    if cached:
        return cached
    tp = request.headers.get("traceparent", "")
    parts = tp.strip().split("-")
    if len(parts) == 4 and len(parts[1]) == 32 and set(parts[1]) <= set("0123456789abcdef") and set(parts[1]) != {"0"}:
        trace_id = parts[1]
    else:
        trace_id = secrets.token_hex(16)
    ctx = (trace_id, f"00-{trace_id}-{secrets.token_hex(8)}-01")
    try:
        request.state.trace_ctx = ctx
    except Exception:                               # the context is still returned: caching it only saves recomputing it
        log.debug("could not cache the trace context on the request", exc_info=True)
    return ctx


# ── demo identity ──
class LoginRequest(BaseModel):
    role: str


@router.post("/demo/login", dependencies=[Depends(ip_rate_limit)])
def demo_login(body: LoginRequest):
    if not ident.demo_mode():
        raise HTTPException(404, "demo login is disabled on this deployment")
    if body.role not in ident.ROLES:
        raise HTTPException(422, f"role must be one of {list(ident.ROLES)}")
    return ident.issue_token(body.role)


@router.get("/me")
def me(identity: Identity | None = Depends(current_identity)):
    if identity is None:
        return {"authenticated": False, "demo_mode": ident.demo_mode(), "roles": list(ident.ROLES)}
    return {"authenticated": True, "user_id": identity.user_id, "role": identity.role, "source": identity.source,
            "label": "demo identity" if identity.is_demo else "trusted proxy identity"}


# ── AI routes ──
def _gateway_block(res, request_id_hint: str | None, trace_id: str) -> dict:
    tr = res.trace or {}
    layers = {k: {"decision": "block" if v.get("blocked") else ("skipped" if v.get("skipped") else "pass"), "latency_ms": round(float(v.get("latency_ms", 0) or 0), 3)}
              for k, v in (tr.get("per_layer") or {}).items()}
    return {"decision": "allow" if res.allowed else "block", "reason": res.block_reason, "phase": tr.get("phase"), "layers": layers,
            "pii_found": tr.get("pii_found"), "latency_ms": round(float(tr.get("total_latency_ms", 0) or 0), 2), "trace_id": trace_id}


def _run_ai(request: Request, identity: Identity, path: str, prompt: str, build_body, text_of, route: str) -> dict:
    trace_id, tp = trace_context(request)
    adapter = CapturingAdapter(deps.p2_client(), path, build_body, text_of, identity, tp)
    mw = deps.middleware
    session = hashlib.sha256(f"{identity.user_id}|{route}".encode()).hexdigest()[:24]
    t0 = time.perf_counter()
    try:
        res = mw.process(prompt=prompt, session_id=session, backend=adapter, role=identity.role, system_prompt="", user_id=identity.user_id,
                         trace_id=trace_id, route=route)
    except UpstreamError as exc:
        raise HTTPException(exc.status, exc.detail)
    gw = _gateway_block(res, None, trace_id)
    from gateway.v1.otel import emit_spans
    emit_spans(trace_id, route, (res.trace or {}).get("per_layer", {}), gw["decision"], (time.perf_counter() - t0) * 1000)
    gw["gateway_latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    record_request(route, gw["decision"], (time.perf_counter() - t0))
    if not res.allowed and adapter.result is None:                       # stopped before the assistant was called
        return {"blocked": True, "gateway": gw, "answer": None}
    body = dict(adapter.result or {})
    if not res.allowed:                                                  # post-flight stopped the assistant's answer
        return {"blocked": True, "gateway": gw, "answer": res.response_text}
    body["gateway"], body["blocked"] = gw, False
    return body


class AskContext(BaseModel):
    page: str | None = None
    case_id: str | None = None
    supplier_id: str | None = None
    control: str | None = None
    filters: dict | None = None
    as_of: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    context: AskContext = Field(default_factory=AskContext)
    conversation_id: str | None = None


@router.post("/ask", dependencies=[Depends(ip_rate_limit)])
def ask(req: AskRequest, request: Request, identity: Identity = Depends(require_identity)):
    return _run_ai(request, identity, "/v1/ask", req.question,
                   lambda text: {"question": text, "context": req.context.model_dump(), "conversation_id": req.conversation_id},
                   lambda r: r.get("answer") or "", "ask")


class InvestigationRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    context: AskContext = Field(default_factory=AskContext)


@router.post("/investigations", dependencies=[Depends(ip_rate_limit)])
def investigate(req: InvestigationRequest, request: Request, identity: Identity = Depends(require_identity)):
    def text_of(r):
        return " ".join([r.get("summary", "")] + [c.get("text", "") for c in r.get("root_causes", [])] + [c.get("text", "") for c in r.get("recommendations", [])])
    return _run_ai(request, identity, "/v1/investigations", req.question,
                   lambda text: {"question": text, "context": req.context.model_dump()}, text_of, "investigation")


class NLFilterRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)


@router.post("/nl-filter", dependencies=[Depends(ip_rate_limit)])
def nl_filter(req: NLFilterRequest, request: Request, identity: Identity = Depends(require_identity)):
    return _run_ai(request, identity, "/v1/nl-filter", req.question, lambda text: {"question": text}, lambda r: r.get("restatement") or r.get("reason") or "", "nl-filter")


class BriefingRequest(BaseModel):
    as_of: str | None = None


@router.post("/briefing", dependencies=[Depends(ip_rate_limit)])
def briefing(req: BriefingRequest, request: Request, identity: Identity = Depends(require_identity)):
    trace_id, tp = trace_context(request)
    out = _passthrough("POST", "/v1/briefing", identity, tp, {"as_of": req.as_of}).json()
    out["gateway"] = {"decision": "allow", "layers": {}, "note": "no user-supplied text to inspect; response checked for leaks only", "trace_id": trace_id}
    return out


def _passthrough(method: str, path: str, identity: Identity, tp: str, body=None, params=None):
    try:
        r = deps.p2_client().request(method, path, identity, tp, body, params)
    except UpstreamError as exc:
        raise HTTPException(exc.status, exc.detail)
    return r


@router.get("/investigations")
def list_investigations(request: Request, limit: int = 50, identity: Identity = Depends(require_identity)):
    _, tp = trace_context(request)
    return _passthrough("GET", "/v1/investigations", identity, tp, params={"limit": limit}).json()


@router.get("/investigations/{inv_id}")
def get_investigation(inv_id: str, request: Request, identity: Identity = Depends(require_identity)):
    _, tp = trace_context(request)
    return _passthrough("GET", f"/v1/investigations/{inv_id}", identity, tp).json()


@router.get("/investigations/{inv_id}/export")
def export_investigation(inv_id: str, request: Request, format: str = Query("md", pattern="^(md|pdf)$"), identity: Identity = Depends(require_identity)):
    _, tp = trace_context(request)
    r = _passthrough("GET", f"/v1/investigations/{inv_id}/export", identity, tp, params={"format": format})
    return Response(r.content, media_type=r.headers.get("content-type", "text/markdown"))


# ── interventions (proposals go P2 -> this gateway's firewall; humans approve here or via P2) ──
@router.post("/interventions")
def propose(request: Request, body: dict, identity: Identity = Depends(require_identity)):
    _, tp = trace_context(request)
    return _passthrough("POST", "/v1/interventions", identity, tp, body).json()


@router.get("/interventions")
def list_interventions(request: Request, status: str | None = None, identity: Identity = Depends(require_identity)):
    _, tp = trace_context(request)
    return _passthrough("GET", "/v1/interventions", identity, tp, params={"status": status} if status else None).json()


@router.get("/interventions/{iid}")
def get_intervention(iid: int, request: Request, identity: Identity = Depends(require_identity)):
    _, tp = trace_context(request)
    return _passthrough("GET", f"/v1/interventions/{iid}", identity, tp).json()


@router.post("/interventions/{iid}/{verb}")
def decide_intervention(iid: int, verb: str, request: Request, body: dict | None = None, identity: Identity = Depends(require_identity)):
    if verb not in ("approve", "reject", "outcome"):
        raise HTTPException(404, "unknown action")
    _, tp = trace_context(request)
    return _passthrough("POST", f"/v1/interventions/{iid}/{verb}", identity, tp, body or {}).json()


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str, request: Request, identity: Identity = Depends(require_identity)):
    """Gateway decisions for this trace plus the assistant's own spans (P1 calls, retrieval, model, claim gate)."""
    deps.store.ingest()
    with deps.store._c() as c:
        gw = [dict(r) for r in c.execute("SELECT ts, request_id, phase, decision, layer, rule_id, latency_ms FROM decisions WHERE trace_id = ? ORDER BY ts", (trace_id,))]
        acts = [dict(r) for r in c.execute("SELECT ts, tool, effect, stage, rule FROM actions WHERE trace_id = ? ORDER BY ts", (trace_id,))]
    p2 = None
    try:
        p2 = deps.p2_client().request("GET", f"/v1/traces/{trace_id}", identity, None).json()
    except UpstreamError:
        pass
    if not gw and not acts and p2 is None:
        raise HTTPException(404, "trace not found")
    return {"trace_id": trace_id, "gateway": {"decisions": gw, "actions": acts}, "assistant": p2}


# ── approvals (the gateway's queue) ──
APPROVAL_READERS = ("manager", "admin", "finance")


@router.get("/approvals")
def list_approvals(status: str | None = Query(None, pattern="^(pending|approved|denied)$"), limit: int = 100,
                   identity: Identity = Depends(require_roles(*APPROVAL_READERS))):
    return {"approvals": get_firewall().approvals.list_requests(status, min(max(limit, 1), 500))}


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, identity: Identity = Depends(require_roles(*APPROVAL_READERS))):
    row = get_firewall().approvals.get(approval_id)
    if row is None:
        raise HTTPException(404, "not found")
    return row


class DecideBody(BaseModel):
    note: str = Field(default="", max_length=300)


def _decide(approval_id: str, approve: bool, body: DecideBody, identity: Identity):
    try:
        return get_firewall().approvals.decide(approval_id, approve, Principal(identity.role, identity.user_id), body.note)
    except ApprovalNotFound as exc:
        raise HTTPException(404, str(exc))
    except ApprovalForbidden as exc:
        raise HTTPException(403, str(exc))
    except ApprovalConflict as exc:
        raise HTTPException(409, str(exc))
    except ApprovalError as exc:
        raise HTTPException(400, str(exc))


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, body: DecideBody, identity: Identity = Depends(require_roles("manager", "admin"))):
    return _decide(approval_id, True, body, identity)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, body: DecideBody, identity: Identity = Depends(require_roles("manager", "admin"))):
    return _decide(approval_id, False, body, identity)


# ── governance ──
@router.get("/governance/summary")
def governance_summary(window: str = Query("1h")):
    if window not in WINDOWS:
        raise HTTPException(422, f"window must be one of {sorted(WINDOWS)}")
    return deps.store.summary(window)


@router.get("/governance/events")
def governance_events(limit: int = Query(100, ge=1, le=500), identity: Identity | None = Depends(current_identity)):
    admin = identity is not None and identity.role == "admin"
    return {"detail_level": "full" if admin else "redacted", "note": None if admin else "Session ids are hashed and reasons are withheld below the admin role.",
            "events": deps.store.events(limit, admin)}


def _constraint_text(c: dict) -> str:
    parts = []
    for k in ("in", "equals", "pattern", "max_length", "min", "max", "type", "required"):
        if k in c:
            v = c[k]
            parts.append(f"{k}: {v if not isinstance(v, str) or len(v) < 60 else v[:57] + '...'}")
    return ", ".join(parts)


@router.get("/governance/policy-matrix")
def policy_matrix():
    pol = get_firewall().policy
    roles = ["employee", "viewer", "analyst", "finance", "manager", "admin"]
    tools = []
    for name, spec in pol.tools.items():
        cells = {}
        for role in roles:
            rules = [r for r in spec.get("rules", []) if role in r["roles"]]
            if not rules:
                cells[role] = {"effect": "deny", "rules": []}
                continue
            needs = any(r.get("approval") == "required" for r in rules)
            cells[role] = {"effect": "approval" if needs else "allow",
                           "rules": [{"name": r.get("name", f"rule-{i}"), "approval": r.get("approval", "none"),
                                      "require_role_separation": bool(r.get("require_role_separation")),
                                      "args": {a: _constraint_text(c) for a, c in (r.get("args") or {}).items()}} for i, r in enumerate(rules)]}
        tools.append({"tool": name, "kind": spec["kind"], "output_trust": spec.get("output_trust", "untrusted"),
                      "taint": spec.get("taint") or {}, "max_per_session": spec.get("max_per_session"), "cells": cells})
    return {"roles": roles, "tools": tools, "default_effect": pol.default_effect, "strict_args": pol.strict_args,
            "policy_sha256": hashlib.sha256(Path(DEFAULT_POLICY_PATH).read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16],
            "provenance": "measured", "source": "config/tool_policies.yaml"}


def _catalog() -> dict:
    return yaml.safe_load((ROOT / "config" / "rules_catalog.yaml").read_text(encoding="utf-8"))


@router.get("/rules")
def rules():
    cat = _catalog()
    atlas = cat.get("default_atlas_for_owasp", {})
    out = []
    for det in cat["detectors"].values():
        for rid, r in det["rules"].items():
            owasp = r.get("owasp") or "LLM01"
            out.append({"rule_id": rid, "name": r["name"], "layer": det["layer"], "owasp": owasp, "atlas": r.get("atlas") or atlas.get(owasp), "atlas_verified": False,
                        "redteam": r.get("redteam"), "kind": "detector"})
    for stage, r in cat["action_firewall"]["stages"].items():
        owasp = r.get("owasp", "LLM06")
        out.append({"rule_id": f"action:{stage}", "name": r["name"], "layer": "action_firewall", "owasp": owasp, "atlas": atlas.get(owasp), "atlas_verified": False,
                    "redteam": r.get("redteam"), "kind": "action_stage"})
    return {"rules": out, "redteam_report": cat.get("redteam_report"), "note": "ATLAS technique ids are pointers and are not verified against the live ATLAS matrix."}


@router.get("/config")
def config():
    from gateway import middleware as mw_mod
    from gateway import model_integrity
    mw = deps.middleware
    hashes = {}
    try:
        manifest = model_integrity.load_manifest()
        hashes = {k: v[:16] for k, v in manifest.items()}
    except Exception:
        hashes = {}
    return {"detector_backend": {"classifier": getattr(mw, "classifier_backend", None), "embedding": getattr(mw, "embedding_backend", None),
                                 "lite_mode": getattr(mw, "lite_mode", None)},
            "thresholds": {"classifier": mw_mod.CLASSIFIER_THRESHOLD},
            "policy": {"sha256": hashlib.sha256(Path(DEFAULT_POLICY_PATH).read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16], "default_effect": "deny"},
            "model_integrity": {"mode": model_integrity.mode(), "artifact_hashes": hashes},
            "demo_mode": ident.demo_mode(), "assistant_configured": deps.p2_client().configured}


@router.get("/services")
def services():
    """Reachability of what this gateway fronts, for the console's health page."""
    out = {"gateway": {"status": "ok", "demo_mode": ident.demo_mode()}}
    p2 = deps.p2_client()
    if not p2.configured:
        out["assistant"] = {"configured": False, "reachable": False, "detail": "P2_URL is not set"}
    else:
        try:
            r = p2.http.get(p2.base + "/health", timeout=5)
            out["assistant"] = {"configured": True, "reachable": r.status_code == 200, "detail": f"HTTP {r.status_code}"}
        except Exception as exc:
            out["assistant"] = {"configured": True, "reachable": False, "detail": type(exc).__name__}
    return out


# ── attack lab ──
_LAB_HITS: dict[str, list[float]] = {}


def _lab_limit(identity: Identity):
    limit = int(os.environ.get("GATEWAY_LAB_RATE_LIMIT", "20"))
    now = time.time()
    hits = [t for t in _LAB_HITS.get(identity.user_id, []) if now - t < 60]
    if len(hits) >= limit:
        raise HTTPException(429, f"attack lab limited to {limit} runs per minute")
    hits.append(now)
    _LAB_HITS[identity.user_id] = hits


@router.get("/lab/scenarios")
def lab_scenarios():
    return lab.list_scenarios()


class LabRun(BaseModel):
    scenario_id: str
    defenses: str = Field(default="on", pattern="^(on|off)$")
    target: str = Field(default="stub", pattern="^(stub|p2)$")


@router.post("/lab/run", dependencies=[Depends(ip_rate_limit)])
def lab_run(body: LabRun, request: Request, identity: Identity = Depends(require_identity)):
    _lab_limit(identity)
    p2_backend = None
    if body.target == "p2":
        if body.defenses == "off":
            raise HTTPException(422, "defenses can only be turned off against the undefended stub")
        if not deps.p2_client().configured:
            raise HTTPException(501, "target p2 needs P2_URL configured; use target=stub")
        _, tp = trace_context(request)
        p2_backend = CapturingAdapter(deps.p2_client(), "/v1/ask", lambda text: {"question": text, "context": {"page": "overview"}}, lambda r: r.get("answer") or "", identity, tp)
    out = lab.run(body.scenario_id, body.defenses == "on", body.target, deps.middleware, p2_backend=p2_backend, role="employee")
    if out is None:
        raise HTTPException(404, "unknown scenario")
    record_request("lab", "block" if not out.get("compromised") else "allow", out.get("latency_ms", 0) / 1000)
    return out


# ── metrics ──
try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
    _REQ = Counter("gateway_v1_requests_total", "v1 requests by route and gateway decision", ["route", "decision"])
    _LAT = Histogram("gateway_v1_request_seconds", "v1 request latency", ["route"])
    _PENDING = Gauge("gateway_approvals_pending", "Approvals awaiting a human decision")
except Exception:                                                        # prometheus_client is optional
    _REQ = _LAT = _PENDING = None


def record_request(route: str, decision: str, seconds: float) -> None:
    if _REQ is not None:
        _REQ.labels(route=route, decision=decision).inc()
        _LAT.labels(route=route).observe(max(seconds, 0.0))


def metrics_endpoint() -> Response:
    if _REQ is None:
        return PlainTextResponse("prometheus_client not installed\n", status_code=501)
    try:
        _PENDING.set(get_firewall().approvals.count_pending())
    except Exception:                               # a scrape must not fail because the approvals store is unavailable: the gauge keeps its last value
        log.warning("pending-approvals gauge not updated", exc_info=True)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def install(app, middleware) -> None:
    deps.middleware = middleware
    origins = [o.strip() for o in os.environ.get("GATEWAY_CORS_ORIGINS", "").split(",") if o.strip()]
    if origins:                                    # browsers call /v1 directly in production; in dev the console proxies instead
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type", "traceparent"], expose_headers=["X-Trace-ID"])

    @app.middleware("http")
    async def _trace_header(request: Request, call_next):
        trace_id, _ = trace_context(request)
        response = await call_next(request)
        if request.url.path.startswith("/v1") or request.url.path.startswith("/gateway"):
            response.headers["X-Trace-ID"] = trace_id
        return response

    app.add_api_route("/metrics", metrics_endpoint, methods=["GET"], include_in_schema=False)
    app.include_router(router)
