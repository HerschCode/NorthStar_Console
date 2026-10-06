"""Working-capital metrics on the AP process (finance module Phase F4).

Definitions used here (stated, not textbook-precise -- there is no ledger AP balance or COGS in
this dataset, only case-level events and a running order value, so these are transaction-level
proxies, not the balance-sheet ratio a treasury team would compute from the GL):
- invoice_to_clear_days: days from the case's first invoice-related event (whichever of
  "Vendor creates invoice" / "Record Invoice Receipt" comes first) to "Clear Invoice". This is
  the closest available proxy for "days payable outstanding" at the transaction level.
- late: invoice_to_clear_days > payment_terms_days (config/ap_controls.yaml, assumption).
- early-payment discount: 2/10 net 30 (assumption) -- captured today = discount value for cases
  actually cleared within the discount window; potential = discount value if EVERY eligible case
  had cleared within the window. The gap between them is the missed-discount opportunity.
"""
from __future__ import annotations

import pandas as pd

from src.controls.case_attributes import case_ap_attributes


def _invoice_and_clear_times(events: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    acts = cfg["activities"]
    inv = events[events["activity"].isin([acts["vendor_invoice"], acts["invoice_receipt"]])]
    inv_time = inv.groupby("case_id")["timestamp"].min().rename("invoice_time")
    clear = events[events["activity"] == acts["clear_invoice"]]
    clear_time = clear.groupby("case_id")["timestamp"].min().rename("clear_time")
    return pd.concat([inv_time, clear_time], axis=1).dropna()


def invoice_to_clear_days(events: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """One row per case with a completed invoice->clear cycle: case_id, days, plus the case's
    supplier_id / category / sub_spend_area / order_value_eur for grouping."""
    times = _invoice_and_clear_times(events, cfg)
    times["days"] = (times["clear_time"] - times["invoice_time"]).dt.total_seconds() / 86400
    times = times[times["days"] >= 0].reset_index()   # negative = out-of-order data, excluded, not silently kept

    attrs = case_ap_attributes(events)
    keep_cols = [c for c in ("supplier_id", "category", "sub_spend_area", "order_value_eur") if c in attrs.columns]
    return times.merge(attrs[["case_id"] + keep_cols], on="case_id", how="left")


def days_payable_outstanding_by(events: pd.DataFrame, cfg: dict, group_col: str) -> pd.DataFrame:
    df = invoice_to_clear_days(events, cfg)
    if group_col not in df.columns:
        return pd.DataFrame(columns=[group_col, "case_count", "mean_days", "median_days"])
    g = df.groupby(group_col)["days"].agg(case_count="count", mean_days="mean", median_days="median").reset_index()
    return g.sort_values("mean_days", ascending=False)


def late_payment_exposure(events: pd.DataFrame, cfg: dict) -> dict:
    df = invoice_to_clear_days(events, cfg)
    if df.empty:
        return {"n_cases": 0, "n_late": 0, "late_rate_pct": None, "exposure_eur": 0.0, "payment_terms_days": cfg["payment_terms_days"]}
    late = df[df["days"] > cfg["payment_terms_days"]]
    return {
        "n_cases": int(len(df)), "n_late": int(len(late)),
        "late_rate_pct": round(100 * len(late) / len(df), 1),
        "exposure_eur": round(float(late["order_value_eur"].fillna(0).sum()), 2),
        "payment_terms_days": cfg["payment_terms_days"],
    }


def early_payment_discount_scenario(events: pd.DataFrame, cfg: dict) -> dict:
    df = invoice_to_clear_days(events, cfg)
    eligible = df.dropna(subset=["order_value_eur"])
    if eligible.empty:
        return {"n_eligible": 0, "captured_discount_eur": 0.0, "potential_discount_eur": 0.0, "missed_discount_eur": 0.0,
                "discount_pct": cfg["early_discount_pct"], "discount_window_days": cfg["early_discount_window_days"]}
    within_window = eligible[eligible["days"] <= cfg["early_discount_window_days"]]
    captured = float(within_window["order_value_eur"].sum()) * cfg["early_discount_pct"]
    potential = float(eligible["order_value_eur"].sum()) * cfg["early_discount_pct"]
    return {
        "n_eligible": int(len(eligible)), "n_captured": int(len(within_window)),
        "captured_discount_eur": round(captured, 2), "potential_discount_eur": round(potential, 2),
        "missed_discount_eur": round(potential - captured, 2),
        "discount_pct": cfg["early_discount_pct"], "discount_window_days": cfg["early_discount_window_days"],
    }


def working_capital_summary(events: pd.DataFrame, cfg: dict) -> dict:
    return {
        "dpo_by_supplier": days_payable_outstanding_by(events, cfg, "supplier_id").to_dict(orient="records"),
        "dpo_by_spend_area": days_payable_outstanding_by(events, cfg, "sub_spend_area").to_dict(orient="records"),
        "late_payment": late_payment_exposure(events, cfg),
        "early_discount_scenario": early_payment_discount_scenario(events, cfg),
    }
