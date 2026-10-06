"""/v1 routes of the copilot: ask, investigations, briefing, nl-filter, interventions, traces, spend.

All routes require the service API key (the console calls through the P3 gateway, which holds it). The end user's identity
comes from `X-Northstar-User` / `X-Northstar-Role` set by that trusted gateway; a missing identity is a read-only viewer.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from src.api.auth import require_api_key
from src.v1 import ask as ask_mod
from src.v1 import briefing as briefing_mod
from src.v1 import investigations as inv_mod
from src.v1 import nl_filter as nl_mod
from src.v1.interventions import GatewayClient, Ledger, LedgerError
from src.v1.llm import LLM, get_llm
from src.v1.p1 import P1
from src.v1.spend import SpendExhausted, SpendGuard
from src.v1.trace import Trace, TraceStore, parse_traceparent

ROLES = {"viewer", "analyst", "manager", "finance", "admin"}


class Deps:
    """Wiring in one place so tests can swap any piece."""

    def __init__(self):
        self._llm: LLM | None = None
        self.stores: dict = {}
        self.gateway: GatewayClient | None = None
        self.p1_factory = lambda trace: P1(trace)
        self.retriever = None

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    @llm.setter
    def llm(self, v):
        self._llm = v

    def get_retriever(self):
        if self.retriever is not None:
            return self.retriever
        from src.retrieval.search import hybrid_search
        return lambda q, k: hybrid_search(q, top_k=k)

    def store(self, name: str, factory):
        if name not in self.stores:
            self.stores[name] = factory()
        return self.stores[name]

    def gw(self) -> GatewayClient:
        if self.gateway is None:
            self.gateway = GatewayClient()
        return self.gateway


deps = Deps()
router = APIRouter(prefix="/v1", tags=["v1"], dependencies=[Depends(require_api_key)])


def identity(x_northstar_user: str | None = Header(default=None), x_northstar_role: str | None = Header(default=None)) -> dict:
    role = (x_northstar_role or "viewer").lower()
    return {"user": (x_northstar_user or "anonymous")[:64], "role": role if role in ROLES else "viewer"}


def _trace(request: Request, name: str) -> Trace:
    return Trace(parse_traceparent(request.headers.get("traceparent")), name)


def _finish(trace: Trace) -> None:
    deps.store("traces", TraceStore).save(trace)


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


@router.post("/ask")
def v1_ask(req: AskRequest, request: Request, who: dict = Depends(identity)):
    trace = _trace(request, "ask")
    out = ask_mod.ask(req.model_dump(), llm=deps.llm, p1=deps.p1_factory(trace), retriever=deps.get_retriever(), trace=trace)
    _finish(trace)
    out["conversation_id"] = req.conversation_id
    return out


class InvestigationRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    context: AskContext = Field(default_factory=AskContext)


@router.post("/investigations", status_code=201)
def v1_investigate(req: InvestigationRequest, request: Request, who: dict = Depends(identity)):
    trace = _trace(request, "investigation")
    inv = inv_mod.run_investigation(req.model_dump(), llm=deps.llm, p1=deps.p1_factory(trace), retriever=deps.get_retriever(), trace=trace)
    deps.store("investigations", inv_mod.InvestigationStore).save(inv, who["user"])
    _finish(trace)
    return inv


@router.get("/investigations")
def v1_list_investigations(limit: int = Query(50, ge=1, le=200)):
    return {"investigations": deps.store("investigations", inv_mod.InvestigationStore).list(limit)}


@router.get("/investigations/{inv_id}")
def v1_get_investigation(inv_id: str):
    inv = deps.store("investigations", inv_mod.InvestigationStore).get(inv_id)
    if inv is None:
        raise HTTPException(404, "investigation not found")
    return inv


@router.get("/investigations/{inv_id}/export")
def v1_export_investigation(inv_id: str, format: str = Query("md", pattern="^(md|pdf)$")):
    inv = deps.store("investigations", inv_mod.InvestigationStore).get(inv_id)
    if inv is None:
        raise HTTPException(404, "investigation not found")
    if format == "pdf":
        return Response(inv_mod.to_pdf(inv), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="investigation-{inv_id[:8]}.pdf"'})
    return PlainTextResponse(inv_mod.to_markdown(inv), media_type="text/markdown")


class BriefingRequest(BaseModel):
    as_of: str | None = None


@router.post("/briefing")
def v1_briefing(req: BriefingRequest, request: Request):
    trace = _trace(request, "briefing")
    p1 = deps.p1_factory(trace)
    try:
        facts = p1.get("/v1/briefing", {"as_of": req.as_of} if req.as_of else None)
    except Exception as exc:
        raise HTTPException(503, f"P1 briefing facts unavailable: {str(exc)[:120]}")
    out = briefing_mod.make_brief(facts, deps.llm)
    out["trace_id"] = trace.trace_id
    _finish(trace)
    return out


class NLFilterRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)


@router.post("/nl-filter")
def v1_nl_filter(req: NLFilterRequest):
    return nl_mod.nl_filter(req.question, deps.llm if deps.llm.name != "none" else None)


class ProposeRequest(BaseModel):
    case_id: str
    intervention_type: str = "expedite_approval"
    rationale: str = Field(min_length=3, max_length=500)
    risk: float = Field(ge=0, le=1)
    sources: list[dict] = Field(default_factory=list, description="[{kind, text, trust: trusted|untrusted}] registered with the gateway before authorising")


def _ledger() -> Ledger:
    return deps.store("ledger", Ledger)


def _ledger_call(fn):
    try:
        return fn()
    except LedgerError as exc:
        raise HTTPException(exc.status, str(exc))


@router.post("/interventions", status_code=201)
def v1_propose(req: ProposeRequest, request: Request, who: dict = Depends(identity)):
    tp = request.headers.get("traceparent")
    row = _ledger_call(lambda: _ledger().propose(case_id=req.case_id, intervention_type=req.intervention_type, rationale=req.rationale,
                                                risk=req.risk, identity=who, gateway=deps.gw(), sources=req.sources, trace_header=tp))
    if row["status"] == "approved":                                  # policy allowed it outright
        row = _ledger_call(lambda: _ledger().execute(row["id"], trace_header=tp))
    return row


@router.get("/interventions")
def v1_list_interventions(status: str | None = None, limit: int = Query(100, ge=1, le=500)):
    return {"interventions": _ledger().list(status, limit)}


@router.get("/interventions/{iid}")
def v1_get_intervention(iid: int):
    row = _ledger().get(iid)
    if row is None:
        raise HTTPException(404, "intervention not found")
    return row


class DecideRequest(BaseModel):
    note: str = Field(default="", max_length=300)


@router.post("/interventions/{iid}/approve")
def v1_approve(iid: int, body: DecideRequest, request: Request, who: dict = Depends(identity)):
    row = _ledger_call(lambda: _ledger().decide(iid, True, who, deps.gw(), body.note, trace_header=request.headers.get("traceparent")))
    return _ledger_call(lambda: _ledger().execute(iid, trace_header=request.headers.get("traceparent"))) if row["status"] == "approved" else row


@router.post("/interventions/{iid}/reject")
def v1_reject(iid: int, body: DecideRequest, request: Request, who: dict = Depends(identity)):
    return _ledger_call(lambda: _ledger().decide(iid, False, who, deps.gw(), body.note, trace_header=request.headers.get("traceparent")))


class OutcomeRequest(BaseModel):
    breached_after: bool


@router.post("/interventions/{iid}/outcome")
def v1_outcome(iid: int, body: OutcomeRequest, who: dict = Depends(identity)):
    return _ledger_call(lambda: _ledger().record_outcome(iid, body.breached_after, who))


@router.get("/traces/{trace_id}")
def v1_trace(trace_id: str):
    t = deps.store("traces", TraceStore).get(trace_id)
    if t is None:
        raise HTTPException(404, "trace not found (traces are kept 14 days)")
    return t


@router.get("/spend")
def v1_spend():
    return deps.store("spend", SpendGuard).status()


@router.get("/limits")
def v1_limits():
    """Where the free-tier models stand right now: per model, key present or not, used this minute / today, any cooldown with its scope
    and when it clears. Provider quotas are authoritative (their 429s set the cooldowns); the caps shown are the ones you configured."""
    llm = deps.llm
    rows = llm.status() if hasattr(llm, "status") else [{"provider": llm.name, "model": llm.name, "state": "ok" if llm.name != "none" else "no_key", "note": "this provider has no per-model free-tier tracking"}]
    free = [r for r in rows if r.get("provider") in ("gemini", "groq")]
    usable = [r for r in free if r.get("state") == "ok"]
    return {"mode": "free-chain" if hasattr(llm, "status") else llm.name, "models": rows,
            "summary": ("all configured free models are available" if usable and len(usable) == len(free) else
                        "some free models are cooling down or have no key" if usable else
                        "no free model can answer right now" if free else "no free-tier key is configured (set GEMINI_API_KEY and/or GROQ_API_KEY)"),
            "next_available_s": min((r["retry_after_s"] for r in free if r.get("state") == "cooling" and r.get("retry_after_s") is not None), default=None),
            "notes": ["Gemini quotas are per project and per model; daily quotas reset at midnight Pacific time.",
                      "Groq reports remaining quota in x-ratelimit-* headers (limit-requests is per day, limit-tokens per minute).",
                      "Neither provider publishes fixed free-tier numbers in its docs: copy yours from Google AI Studio and the Groq console into config/free_models.yaml."]}


def install(app) -> None:
    """Register the 429 handler for exhausted spend caps and mount the router."""
    @app.exception_handler(SpendExhausted)
    async def _spend(_request, exc: SpendExhausted):
        return JSONResponse(status_code=429, content={"detail": str(exc), "scope": exc.scope, "limit_usd": exc.limit})

    app.include_router(router)
