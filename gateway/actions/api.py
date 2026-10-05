"""
HTTP surface of the action firewall.

  POST /gateway/actions/sources          register text that entered the agent's context (user message, document, ...)
  POST /gateway/actions/observe          register a tool result (trust comes from the policy)
  POST /gateway/actions/authorize        policy decision point: allow / deny / require_approval for one tool call
  GET  /gateway/actions/approvals        list requests (?status=pending|approved|denied)
  GET  /gateway/actions/approvals/{id}   one request with its evidence
  POST /gateway/actions/approvals/{id}/approve | /deny     decide (trusted identity, or X-Approver-Token in demo mode)
  GET  /gateway/actions/policy           the tools in the policy and which roles may see them

In demo mode, deciding an approval fails closed if GATEWAY_APPROVER_TOKEN is not set; the caller supplies
approver_id / approver_role and the token only proves the caller may act as an approver at all. With
GATEWAY_REQUIRE_IDENTITY enabled, the trusted proxy supplies the principal instead.
"""
import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.auth import TrustedIdentity, require_trusted_identity, scope_session_id
from gateway.actions.approvals import (
    ApprovalConflict,
    ApprovalError,
    ApprovalForbidden,
    ApprovalNotFound,
    ApprovalQueue,
)
from gateway.actions.firewall import ActionFirewall
from gateway.actions.policy import Principal
from gateway.ip_limits import ip_rate_limit

router = APIRouter(dependencies=[Depends(require_trusted_identity)])
_firewall: ActionFirewall | None = None


def get_firewall() -> ActionFirewall:
    """Lazily built singleton. Storage paths can be overridden with GATEWAY_APPROVALS_DB and
    GATEWAY_ACTIONS_AUDIT (used by demos and tests so they never touch real logs)."""
    global _firewall
    if _firewall is None:
        db, audit = os.environ.get("GATEWAY_APPROVALS_DB"), os.environ.get("GATEWAY_ACTIONS_AUDIT")
        _firewall = ActionFirewall(
            approvals=ApprovalQueue(db) if db else None,
            **({"audit_path": audit} if audit else {}),
        )
    return _firewall


def set_firewall(fw: ActionFirewall | None):
    """Test hook / custom wiring."""
    global _firewall
    _firewall = fw


class SourceRequest(BaseModel):
    session_id: str
    kind: str = Field(description="e.g. user_message, retrieved_document, upload")
    text: str
    trust: str = Field(pattern="^(trusted|untrusted)$")


class ObserveRequest(BaseModel):
    session_id: str
    tool: str
    result: str


class AuthorizeRequest(BaseModel):
    session_id: str
    role: str = "employee"
    user_id: str = "unknown"
    tool: str
    args: dict = Field(default_factory=dict)


class DecideRequest(BaseModel):
    approver_id: str | None = None
    approver_role: str | None = "manager"
    note: str = ""


def _require_token(token: str | None):
    expected = os.environ.get("GATEWAY_APPROVER_TOKEN")
    if not expected:
        raise HTTPException(503, "approvals are disabled: GATEWAY_APPROVER_TOKEN is not set")
    if not token or len(token) > 4096 or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(401, "missing or invalid X-Approver-Token")


def _require_approval_reader(identity: TrustedIdentity | None):
    if identity is not None and identity.role not in ("manager", "admin"):
        raise HTTPException(403, "manager or admin role required to inspect approvals")


@router.post("/gateway/actions/sources", dependencies=[Depends(ip_rate_limit)])
def add_source(req: SourceRequest, identity: TrustedIdentity | None = Depends(require_trusted_identity)):
    get_firewall().register_source(scope_session_id(req.session_id, identity), req.kind, req.text, req.trust)
    return {"ok": True}


@router.post("/gateway/actions/observe", dependencies=[Depends(ip_rate_limit)])
def observe(req: ObserveRequest, identity: TrustedIdentity | None = Depends(require_trusted_identity)):
    get_firewall().observe_result(scope_session_id(req.session_id, identity), req.tool, req.result)
    return {"ok": True}


@router.post("/gateway/actions/authorize", dependencies=[Depends(ip_rate_limit)])
def authorize(req: AuthorizeRequest, request: Request, identity: TrustedIdentity | None = Depends(require_trusted_identity)):
    session_id = scope_session_id(req.session_id, identity)
    principal = Principal(identity.role, identity.user_id) if identity else Principal(req.role, req.user_id)
    parts = request.headers.get("traceparent", "").strip().split("-")       # W3C traceparent: one id across console, gateway, assistant, P1
    trace_id = parts[1] if len(parts) == 4 and len(parts[1]) == 32 else None
    return get_firewall().authorize(session_id, principal, req.tool, req.args, trace_id=trace_id).to_dict()


@router.get("/gateway/actions/approvals")
def list_approvals(
    status: str | None = None,
    limit: int = 100,
    identity: TrustedIdentity | None = Depends(require_trusted_identity),
):
    _require_approval_reader(identity)
    if status not in (None, "pending", "approved", "denied"):
        raise HTTPException(400, "status must be pending, approved or denied")
    return get_firewall().approvals.list_requests(status, min(max(limit, 1), 500))


@router.get("/gateway/actions/approvals/{approval_id}")
def get_approval(approval_id: str, identity: TrustedIdentity | None = Depends(require_trusted_identity)):
    _require_approval_reader(identity)
    row = get_firewall().approvals.get(approval_id)
    if row is None:
        raise HTTPException(404, "not found")
    return row


def _decide(
    approval_id: str,
    approve: bool,
    req: DecideRequest,
    token: str | None,
    identity: TrustedIdentity | None,
):
    if identity is None:
        _require_token(token)
        if not req.approver_id or not req.approver_role:
            raise HTTPException(422, "approver_id and approver_role are required without trusted identity")
        approver = Principal(req.approver_role, req.approver_id)
    else:
        approver = Principal(identity.role, identity.user_id)
    try:
        return get_firewall().approvals.decide(approval_id, approve, approver, req.note)
    except ApprovalNotFound as e:
        raise HTTPException(404, str(e))
    except ApprovalForbidden as e:
        raise HTTPException(403, str(e))
    except ApprovalConflict as e:
        raise HTTPException(409, str(e))
    except ApprovalError as e:  # pragma: no cover - future subclasses
        raise HTTPException(400, str(e))


@router.post("/gateway/actions/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    req: DecideRequest,
    x_approver_token: str | None = Header(default=None),
    identity: TrustedIdentity | None = Depends(require_trusted_identity),
):
    _require_approval_reader(identity)
    return _decide(approval_id, True, req, x_approver_token, identity)


@router.post("/gateway/actions/approvals/{approval_id}/deny")
def deny(
    approval_id: str,
    req: DecideRequest,
    x_approver_token: str | None = Header(default=None),
    identity: TrustedIdentity | None = Depends(require_trusted_identity),
):
    _require_approval_reader(identity)
    return _decide(approval_id, False, req, x_approver_token, identity)


@router.get("/gateway/actions/policy")
def policy_summary():
    pol = get_firewall().policy
    return {name: {"kind": spec["kind"], "output_trust": spec.get("output_trust", "untrusted"),
                   "roles": sorted({r for rule in spec.get("rules", []) for r in rule["roles"]}),
                   "approval": any(rule.get("approval") == "required" for rule in spec.get("rules", []))}
            for name, spec in pol.tools.items()}
