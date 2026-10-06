"""P1 `/v1` access for the copilot: context pre-fetch (what the user is looking at) with trace spans."""
from __future__ import annotations

import time
from typing import Callable

from src.tools import client as p1_client
from src.tools.client import OpsPerformanceUnavailable
from src.v1.trace import Trace


class P1:
    def __init__(self, trace: Trace | None = None, getter: Callable | None = None):
        self.trace = trace
        self._get = getter or p1_client.get

    def get(self, path: str, params: dict | None = None) -> dict:
        headers = {"traceparent": self.trace.traceparent} if self.trace else None
        t0 = time.perf_counter()
        try:
            out = self._get(path, params, extra_headers=headers) if headers else self._get(path, params)
            ok = True
            return out
        except OpsPerformanceUnavailable:
            ok = False
            raise
        finally:
            if self.trace:
                self.trace.add(f"p1 GET {path}", "p1", round((time.perf_counter() - t0) * 1000, 1), endpoint=path, ok=ok)

    def context(self, ctx: dict) -> tuple[list[tuple[str, dict]], list[str]]:
        """(endpoint, object) pairs for the page the user is on, plus notes for anything unavailable."""
        as_of = ctx.get("as_of")
        params = {"as_of": as_of} if as_of else None
        wanted: list[str] = []
        if ctx.get("case_id"):
            wanted.append(f"/v1/cases/{ctx['case_id']}")
        if ctx.get("supplier_id"):
            wanted.append(f"/v1/suppliers/{ctx['supplier_id']}")
        page = ctx.get("page")
        if page in (None, "overview", "queue") or not wanted:
            wanted.append("/v1/overview")
        if page == "finance" or ctx.get("control"):
            wanted.append("/v1/finance/controls")
        if page == "models":
            wanted.append("/v1/models")
        if page == "process":
            wanted.append("/v1/process/flow")
        out, notes = [], []
        for path in dict.fromkeys(wanted):
            try:
                out.append((path, self.get(path, params if "finance" not in path and "models" not in path else None)))
            except OpsPerformanceUnavailable as exc:
                notes.append(f"{path} unavailable: {str(exc)[:120]}")
        return out, notes
