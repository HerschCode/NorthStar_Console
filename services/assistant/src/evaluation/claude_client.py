"""Thin Anthropic API wrapper with cost tracking and budget enforcement.

Every eval script uses this instead of calling anthropic.Anthropic directly.
It:
  - reads pricing from config/pricing.yaml
  - tracks cumulative spend across a run (raises BudgetExceededError at the cap)
  - appends a cost line to reports/claude_spend.json on close()
  - supports both regular messages and batch API calls

Usage (regular):
    client = ClaudeClient("claude-haiku-4-5", max_cost_usd=2.0, run_label="gate-eval")
    resp = client.message(system="...", user="...", max_tokens=200)
    text = resp.content[0].text

Usage (batch):
    batch_id = client.batch_submit(requests)   # list of BatchRequest dicts
    results = client.batch_poll(batch_id)       # blocks until done

Usage (dry-run estimate):
    cost = ClaudeClient.estimate_cost("claude-haiku-4-5", in_tokens=500, out_tokens=100, n=200)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_PRICING_FILE = REPO_ROOT / "config" / "pricing.yaml"
_SPEND_FILE = REPO_ROOT / "reports" / "claude_spend.json"


class BudgetExceededError(RuntimeError):
    pass


def _load_pricing() -> dict:
    return yaml.safe_load(_PRICING_FILE.read_text(encoding="utf-8"))


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    n_calls: int = 0


class ClaudeClient:
    """Anthropic messages client with per-run cost tracking."""

    def __init__(
        self,
        model: str | None = None,
        *,
        max_cost_usd: float = 2.0,
        run_label: str = "unnamed",
        batch: bool = False,
        api_key: str | None = None,
    ):
        import anthropic

        pricing = _load_pricing()
        self.model = model or pricing.get("default_model", "claude-haiku-4-5")
        self._rates = pricing.get("models", {}).get(self.model)
        if self._rates is None:
            raise ValueError(
                f"Model {self.model!r} not in config/pricing.yaml. "
                "Add it or pick a model from: " + ", ".join(pricing.get("models", {}).keys())
            )
        self._batch_discount = pricing.get("batch_discount", 0.5)
        self._max_cost_usd = max_cost_usd
        self._run_label = run_label
        self._batch_mode = batch
        self._usage = _Usage()
        self._run_start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Set it in your environment: "
                "export ANTHROPIC_API_KEY=sk-ant-..."
            )
        self._client = anthropic.Anthropic(api_key=key)

    # ── cost accounting ───────────────────────────────────────────────────────

    def _cost(self, in_tok: int, out_tok: int) -> float:
        scale = self._batch_discount if self._batch_mode else 1.0
        r = self._rates
        return (in_tok / 1_000_000 * r["input_per_million"] +
                out_tok / 1_000_000 * r["output_per_million"]) * scale

    def _check_budget(self, additional: float) -> None:
        if self._usage.cost_usd + additional > self._max_cost_usd:
            raise BudgetExceededError(
                f"Budget cap ${self._max_cost_usd:.2f} would be exceeded "
                f"(current ${self._usage.cost_usd:.4f}, additional ${additional:.4f}). "
                "Increase --max-cost-usd or use --sample to limit rows."
            )

    @staticmethod
    def estimate_cost(
        model: str, in_tokens: int, out_tokens: int, n: int = 1, batch: bool = False
    ) -> float:
        pricing = _load_pricing()
        rates = pricing.get("models", {}).get(model)
        if rates is None:
            raise ValueError(f"Unknown model: {model}")
        discount = pricing.get("batch_discount", 0.5) if batch else 1.0
        return (
            in_tokens / 1_000_000 * rates["input_per_million"] +
            out_tokens / 1_000_000 * rates["output_per_million"]
        ) * discount * n

    # ── messages API ─────────────────────────────────────────────────────────

    def message(
        self,
        *,
        user: str,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        pre_check_tokens: int | None = None,
    ):
        """Make one synchronous messages call. Returns raw Anthropic response."""
        if pre_check_tokens is not None:
            est = self._cost(pre_check_tokens, max_tokens)
            self._check_budget(est)

        msgs = [{"role": "user", "content": user}]
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=msgs,
        )
        if system:
            kwargs["system"] = system

        resp = self._client.messages.create(**kwargs)
        in_tok = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
        call_cost = self._cost(in_tok, out_tok)

        self._check_budget(call_cost)
        self._usage.input_tokens += in_tok
        self._usage.output_tokens += out_tok
        self._usage.cost_usd += call_cost
        self._usage.n_calls += 1
        return resp

    def text(self, *, user: str, system: str | None = None, max_tokens: int = 512) -> str:
        """Convenience: returns the text of the first content block."""
        resp = self.message(user=user, system=system, max_tokens=max_tokens)
        return resp.content[0].text if resp.content else ""

    # ── batch API ─────────────────────────────────────────────────────────────

    def batch_submit(self, requests: list[dict]) -> str:
        """Submit a message batch. Each request: {custom_id, messages, max_tokens, system?}.
        Returns the batch id.
        """
        batch_requests = []
        for r in requests:
            params: dict[str, Any] = {
                "model": self.model,
                "max_tokens": r.get("max_tokens", 512),
                "messages": r["messages"],
            }
            if r.get("system"):
                params["system"] = r["system"]
            batch_requests.append({
                "custom_id": r["custom_id"],
                "params": params,
            })
        batch = self._client.messages.batches.create(requests=batch_requests)
        return batch.id

    def batch_poll(self, batch_id: str, poll_interval: float = 30.0) -> list[dict]:
        """Block until batch complete. Returns list of {custom_id, result, error?} dicts."""
        while True:
            batch = self._client.messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                break
            time.sleep(poll_interval)

        results = []
        for result in self._client.messages.batches.results(batch_id):
            r: dict[str, Any] = {"custom_id": result.custom_id}
            if result.result.type == "succeeded":
                msg = result.result.message
                in_tok = msg.usage.input_tokens
                out_tok = msg.usage.output_tokens
                call_cost = self._cost(in_tok, out_tok)
                self._usage.input_tokens += in_tok
                self._usage.output_tokens += out_tok
                self._usage.cost_usd += call_cost
                self._usage.n_calls += 1
                r["text"] = msg.content[0].text if msg.content else ""
                r["stop_reason"] = msg.stop_reason
            else:
                r["error"] = str(result.result)
                r["text"] = ""
            results.append(r)
        return results

    # ── spend ledger ─────────────────────────────────────────────────────────

    def close(self, *, script: str = "") -> dict:
        """Append this run's cost to reports/claude_spend.json and return the entry."""
        entry = {
            "date": self._run_start,
            "script": script or self._run_label,
            "run_label": self._run_label,
            "model": self.model,
            "batch": self._batch_mode,
            "n_calls": self._usage.n_calls,
            "input_tokens": self._usage.input_tokens,
            "output_tokens": self._usage.output_tokens,
            "cost_usd": round(self._usage.cost_usd, 4),
        }
        _SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict] = []
        if _SPEND_FILE.exists():
            try:
                existing = json.loads(_SPEND_FILE.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.append(entry)
        _SPEND_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        return entry

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ── properties ───────────────────────────────────────────────────────────

    @property
    def cumulative_cost_usd(self) -> float:
        return self._usage.cost_usd

    @property
    def n_calls(self) -> int:
        return self._usage.n_calls
