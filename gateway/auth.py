"""Trusted identity supplied by an authenticated reverse proxy."""
import hashlib
import hmac
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class TrustedIdentity:
    user_id: str
    role: str


def identity_is_required() -> bool:
    return os.environ.get("GATEWAY_REQUIRE_IDENTITY", "").lower() in ("1", "true", "yes")


def require_trusted_identity(
    x_gateway_identity_token: str | None = Header(default=None, alias="X-Gateway-Identity-Token"),
    x_gateway_user_id: str | None = Header(default=None, alias="X-Gateway-User-ID"),
    x_gateway_role: str | None = Header(default=None, alias="X-Gateway-Role"),
) -> TrustedIdentity | None:
    if not identity_is_required():
        return None

    expected = os.environ.get("GATEWAY_IDENTITY_TOKEN")
    if not expected:
        raise HTTPException(503, "identity authentication is enabled but GATEWAY_IDENTITY_TOKEN is not set")
    if (
        not x_gateway_identity_token
        or len(x_gateway_identity_token) > 4096
        or not hmac.compare_digest(x_gateway_identity_token.encode("utf-8"), expected.encode("utf-8"))
    ):
        raise HTTPException(401, "missing or invalid trusted identity token")

    user_id = (x_gateway_user_id or "").strip()
    role = (x_gateway_role or "").strip()
    if not user_id or len(user_id) > 128 or not role or len(role) > 32:
        raise HTTPException(401, "trusted identity requires a user ID (max 128 characters) and role (max 32)")
    return TrustedIdentity(user_id=user_id, role=role)


def scope_session_id(session_id: str, identity: TrustedIdentity | None) -> str:
    if identity is None:
        return session_id
    scoped = f"{identity.user_id}\0{session_id}".encode("utf-8")
    return hashlib.sha256(scoped).hexdigest()[:32]
