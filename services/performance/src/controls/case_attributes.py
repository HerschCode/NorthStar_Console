"""Case-level AP-control attributes derived from the event log.

item_category, gr_based_inv_verif, goods_receipt_required, document_type, item_type, company,
sub_spend_area and (approximately) order value are all constant per case in the source XES -- they
are trace-level attributes in BPI 2019, carried onto every event of that case by the CSV sampler
(scripts/download_bpi2019_sample.py). Taking the first non-null value per case_id (same pattern
src/transformation/build_process_cases.py already uses for category/supplier_id) recovers them.
"""
from __future__ import annotations

import pandas as pd

CONSTANT_FIELDS = [
    "supplier_id", "user_id", "category", "item_category", "gr_based_inv_verif",
    "goods_receipt_required", "document_type", "item_type", "company", "sub_spend_area",
]


def case_ap_attributes(events: pd.DataFrame) -> pd.DataFrame:
    """One row per case_id with the constant AP fields present in `events`, plus order_value_eur
    (the max net_worth_eur observed across the case's events -- net_worth_eur is a running
    cumulative total per SAP's own field name, so its max within a case is the best available
    proxy for the case's final order value from this single field; there is no separate
    invoiced-amount / received-amount column in this dataset, which C1 and C4 below say
    explicitly rather than pretending otherwise)."""
    grouped = events.groupby("case_id")
    out = pd.DataFrame({"case_id": grouped.size().index})
    for col in CONSTANT_FIELDS:
        if col in events.columns:
            out = out.merge(grouped[col].first().rename(col), on="case_id", how="left")
    if "net_worth_eur" in events.columns:
        out = out.merge(grouped["net_worth_eur"].max().rename("order_value_eur"), on="case_id", how="left")
    if "purchase_order_id" in events.columns:
        out = out.merge(grouped["purchase_order_id"].first().rename("purchase_order_id"), on="case_id", how="left")
    if "start_time" not in events.columns and "timestamp" in events.columns:
        out = out.merge(grouped["timestamp"].min().rename("start_time"), on="case_id", how="left")
    return out


def first_activity_time(events: pd.DataFrame, activity: str) -> pd.Series:
    """case_id -> earliest timestamp of `activity` in that case (NaT if it never occurs)."""
    matches = events[events["activity"] == activity]
    return matches.groupby("case_id")["timestamp"].min()
