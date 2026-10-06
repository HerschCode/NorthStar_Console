"""Markdown model card generated from a registry record, so documentation cannot drift from what was actually promoted."""
from __future__ import annotations

from .registry import ModelRegistry


def render(registry: ModelRegistry, name: str, version: str) -> str:
    rec = registry.record(name, version)
    m = rec["metrics"]
    aliases = [a for a in ("champion", "challenger") if (registry.resolve(name, a) or {}).get("version") == version]
    ci = m.get("roc_auc_ci95")
    lines = [
        f"# Model card: {name} {version}", "",
        f"* Registered: {rec['registered_at']}  ·  aliases now pointing here: {', '.join(aliases) or 'none'}",
        f"* Artifact sha256: `{rec['sha256']}`", f"* Data fingerprint: `{rec.get('data_fingerprint') or 'not recorded'}`", "",
        "## Held-out metrics",
        f"* ROC-AUC {m['roc_auc']:.3f}" + (f" (95% CI {ci[0]:.3f}-{ci[1]:.3f})" if ci else ""),
        f"* Brier {m['brier']:.4f}  ·  ECE {m['ece']:.4f}",
        f"* Precision@{int(m.get('k_share', 0.3) * 100)}% {m['precision_at_k']:.3f}  ·  lift {m['lift_at_k']:.2f}",
        f"* Rows: {m['n_test']}  ·  target base rate {m['base_rate']:.1%}  ·  distinct scores {m['n_unique_scores']}", "",
        "## Read this before using it",
        "* Metrics are on a held-out slice; they say nothing about how often an intervention would change an outcome.",
        "* A base rate near 0% or 100% makes ROC-AUC uninformative; the promotion gate blocks such targets.",
        "* Effect sizes in the ROI views are assumptions unless a randomized holdout has measured them.", "",
        "## Promotion history",
    ]
    for ev in registry.events():
        if ev.get("name") == name and ev["event"] in ("alias_set", "rolled_back", "gate_decision"):
            lines.append(f"* {ev['at']}: {ev['event']} " + ", ".join(f"{k}={v}" for k, v in ev.items() if k not in ("at", "event", "name")))
    return "\n".join(lines) + "\n"
