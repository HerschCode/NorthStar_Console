"""Identity for the /v1 routes.

Two sources, in this order:
  1. the trusted reverse-proxy headers already supported by gateway/auth.py (real deployments);
  2. a signed DEMO token from POST /v1/demo/login, only when GATEWAY_DEMO_MODE=1. It is labelled "demo identity" everywhere:
     it proves nothing about a real person. Tokens are HMAC-SHA256 signed, short-lived and carry a role.

A forged, tampered or expired token is rejected with 401. No identity at all is `None`: public read-only routes accept that,
everything that asks an AI or acts requires an identity.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from gateway.auth import TrustedIdentity, require_trusted_identity

ROLES = ("viewer", "analyst", "manager", "finance", "admin")
TOKEN_TTL_SECONDS = int(os.environ.get("GATEWAY_DEMO_TOKEN_TTL", "3600"))
_PROCESS_SECRET = secrets.token_bytes(32)          # used when GATEWAY_DEMO_TOKEN_SECRET is not set: tokens die with the process


@dataclass(frozen=True)
class Identity:
    user_id: str
    role: str
    source: str                                     # "trusted_proxy" | "demo"

    @property
    def is_demo(self) -> bool:
        return self.source == "demo"


def demo_mode() -> bool:
    return os.environ.get("GATEWAY_DEMO_MODE", "").lower() in ("1", "true", "yes")


def _secret() -> bytes:
    s = os.environ.get("GATEWAY_DEMO_TOKEN_SECRET")
    return s.encode("utf-8") if s else _PROCESS_SECRET


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(role: str, now: float | None = None) -> dict:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    t = time.time() if now is None else now
    user = f"demo-{role}-{secrets.token_hex(2)}"
    payload = {"sub": user, "role": role, "iat": int(t), "exp": int(t) + TOKEN_TTL_SECONDS, "demo": True}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return {"token": f"ns1.{body}.{sig}", "user_id": user, "role": role, "expires_at": payload["exp"], "label": "demo identity"}


def verify_token(token: str, now: float | None = None) -> Identity:
    """Raises HTTPException(401) for anything that is not a valid, unexpired token we issued."""
    try:
        version, body, sig = token.split(".")
        if version != "ns1" or len(token) > 2048:
            raise ValueError("format")
        expected = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig.encode("ascii"), expected.encode("ascii")):
            raise ValueError("signature")
        payload = json.loads(_unb64(body))
        if not payload.get("demo") or payload.get("role") not in ROLES or not payload.get("sub"):
            raise ValueError("claims")
        if (time.time() if now is None else now) >= int(payload["exp"]):
            raise HTTPException(401, "demo token expired")
        return Identity(str(payload["sub"]), payload["role"], "demo")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "invalid demo token")


def current_identity(trusted: TrustedIdentity | None = Depends(require_trusted_identity),
                     authorization: str | None = Header(default=None)) -> Identity | None:
    if trusted is not None:
        return Identity(trusted.user_id, trusted.role, "trusted_proxy")
    if authorization and authorization.lower().startswith("bearer "):
        if not demo_mode():
            raise HTTPException(401, "demo tokens are disabled on this deployment")
        return verify_token(authorization[7:].strip())
    return None


def require_identity(identity: Identity | None = Depends(current_identity)) -> Identity:
    if identity is None:
        raise HTTPException(401, "authentication required: POST /v1/demo/login (demo mode) or use the trusted proxy")
    return identity


def require_roles(*roles: str):
    def dep(identity: Identity = Depends(require_identity)) -> Identity:
        if identity.role not in roles:
            raise HTTPException(403, f"requires one of the roles {sorted(roles)}")
        return identity
    return dep
