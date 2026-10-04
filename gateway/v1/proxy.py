"""Calls from the gateway to the assistant (P2). The gateway is the only caller of P2 on behalf of the console.

P2 authenticates the gateway with a service API key (P2_API_KEY, from the environment, never logged) and is told who the end
user is through X-Northstar-User / X-Northstar-Role. `traceparent` is propagated so one trace id spans the console, this
gateway, P2 and P1.
"""
from __future__ import annotations

import os

import httpx

from gateway.adapters.base import BackendAdapter


class UpstreamError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class P2Client:
    def __init__(self, base_url: str | None = None, http: httpx.Client | None = None):
        self.base = (base_url or os.environ.get("P2_URL") or "").rstrip("/")
        self.http = http or httpx.Client(timeout=float(os.environ.get("P2_TIMEOUT_SECONDS", "90")))

    @property
    def configured(self) -> bool:
        return bool(self.base)

    def _headers(self, identity, traceparent: str | None) -> dict:
        h = {}
        key = os.environ.get("P2_API_KEY")
        if key:
            h["X-API-Key"] = key
        if identity is not None:
            h["X-Northstar-User"], h["X-Northstar-Role"] = identity.user_id, identity.role
        if traceparent:
            h["traceparent"] = traceparent
        return h

    def request(self, method: str, path: str, identity, traceparent: str | None, json_body: dict | None = None, params: dict | None = None) -> httpx.Response:
        if not self.configured:
            raise UpstreamError(503, "the assistant (P2) is not configured on this gateway (set P2_URL)")
        try:
            r = self.http.request(method, self.base + path, json=json_body, params=params, headers=self._headers(identity, traceparent))
        except httpx.HTTPError as exc:
            raise UpstreamError(502, f"the assistant is unreachable ({type(exc).__name__})") from exc
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str(r.json().get("detail", ""))[:300]
            except Exception:
                detail = r.text[:200]
            raise UpstreamError(r.status_code, detail or f"assistant returned {r.status_code}")
        return r


class CapturingAdapter(BackendAdapter):
    """Lets GatewayMiddleware.process() run its full pre/post-flight lifecycle around ONE call to P2: `send` forwards the
    sanitised prompt, keeps P2's JSON body in `.result` and returns the text the post-flight checks should inspect."""
    name = "operations_assistant (northstar /v1)"

    def __init__(self, p2: P2Client, path: str, build_body, text_of, identity, traceparent):
        self.p2, self.path, self.build_body, self.text_of = p2, path, build_body, text_of
        self.identity, self.traceparent = identity, traceparent
        self.result: dict | None = None

    def send(self, prompt: str, session_id: str, role: str = "employee", user_id: str = "unknown") -> str:
        r = self.p2.request("POST", self.path, self.identity, self.traceparent, self.build_body(prompt))
        self.result = r.json()
        return self.text_of(self.result)
