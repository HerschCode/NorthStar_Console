"""Accounts-payable control checks (PLAN.md finance module, Phase F2).

Framing: **internal controls and anomaly triage, not fraud detection.** BPI 2019 has no fraud
labels, so nothing here is validated against a ground truth of actual fraud; every exception is a
rule violation or a statistical anomaly, and docs/ap-controls-evaluation.md measures each rule's
recall and false-positive rate only against PLANTED (synthetic, seeded) anomalies, never against
real data as if it were labeled.

Each check function takes the cleaned events DataFrame (src/cleaning/clean_events.py's output,
which already has item_category/user_id/net_worth_eur etc. -- see
src/ingestion/load_event_log.py:RAW_COLUMN_MAP) and a case-level attribute frame
(src/controls/case_attributes.py:case_ap_attributes), and returns one row per exception with a
common shape: case_id, control_id, severity, exposure_eur, evidence (a dict of the specific
numbers that triggered it, so a reviewer never has to trust a bare flag).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.controls.case_attributes import case_ap_attributes, first_activity_time

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "ap_controls.yaml"

SEVERITY = ("low", "medium", "high")


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text())


def _exception_row(case_id, control_id, severity, exposure_eur, evidence: dict) -> dict:
    return {"case_id": case_id, "control_id": control_id, "severity": severity,
            "exposure_eur": None if exposure_eur is None or pd.isna(exposure_eur) else float(exposure_eur),
            "evidence": evidence}


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["case_id", "control_id", "severity", "exposure_eur", "evidence"])


# ---------------------------------------------------------------------------
# C1: three-way-match violation -- invoice activity with no goods receipt at all in the case.
# ---------------------------------------------------------------------------
def check_c1_three_way_match(events: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Structural check only: for items whose item_category says "3-way match", an invoice was
    recorded/cleared but the case has NO goods-receipt event anywhere in it. Does not compare
    invoiced value to received value -- net_worth_eur is a single cumulative running total per
    case (no separate invoiced-amount / received-amount fields exist in this dataset), so an
    amount-based 3-way-match check would be a false precision claim; C1 stays structural and says
    so, rather than inventing a comparison the data can't support."""
    if "item_category" not in events.columns:
        return _empty()
    acts = cfg["activities"]
    attrs = case_ap_attributes(events)
    three_way = attrs[attrs["item_category"].fillna("").str.contains("3-way", case=False)]
    if three_way.empty:
        return _empty()

    invoice_acts = {acts["vendor_invoice"], acts["invoice_receipt"], acts["clear_invoice"]}
    has_invoice = set(events.loc[events["activity"].isin(invoice_acts), "case_id"])
    has_gr = set(events.loc[events["activity"] == acts["goods_receipt"], "case_id"])

    violating = three_way[three_way["case_id"].isin(has_invoice) & ~three_way["case_id"].isin(has_gr)]
    rows = [_exception_row(r.case_id, "C1_three_way_match", "high", getattr(r, "order_value_eur", None),
                           {"item_category": r.item_category, "has_invoice_event": True, "has_goods_receipt_event": False})
            for r in violating.itertuples()]
    return pd.DataFrame(rows) if rows else _empty()


# ---------------------------------------------------------------------------
# C2: invoice-vs-goods-receipt ORDER contradicts what item_category says it should be.
# ---------------------------------------------------------------------------
def check_c2_invoice_gr_order(events: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    acts = cfg["activities"]
    attrs = case_ap_attributes(events)
    labelled = attrs[attrs.get("item_category", pd.Series(dtype=str)).fillna("").str.contains("before|after", case=False, regex=True)]
    if labelled.empty:
        return _empty()

    gr_time = first_activity_time(events, acts["goods_receipt"])
    inv_events = events[events["activity"].isin([acts["vendor_invoice"], acts["invoice_receipt"]])]
    inv_time = inv_events.groupby("case_id")["timestamp"].min()

    rows = []
    for r in labelled.itertuples():
        gr, inv = gr_time.get(r.case_id), inv_time.get(r.case_id)
        if pd.isna(gr) or pd.isna(inv):
            continue
        says_before = "before" in r.item_category.lower()   # "invoice before GR" is the expected order
        actual_before = inv < gr
        if says_before != actual_before:
            exposure = getattr(r, "order_value_eur", None)
            rows.append(_exception_row(r.case_id, "C2_invoice_gr_order_mismatch", "medium", exposure,
                                       {"item_category": r.item_category, "goods_receipt_time": str(gr),
                                        "invoice_time": str(inv), "expected_invoice_before_gr": says_before}))
    return pd.DataFrame(rows) if rows else _empty()


# ---------------------------------------------------------------------------
# C3: approval-threshold splitting.
# ---------------------------------------------------------------------------
def check_c3_threshold_splitting(events: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    attrs = case_ap_attributes(events)
    need = {"supplier_id", "user_id", "order_value_eur", "start_time"}
    if not need <= set(attrs.columns):
        return _empty()
    threshold = cfg["approval_threshold_eur"]
    window = f"{cfg['split_window_days']}D"
    # Two real bugs found running this against the live sample (2026-09-28): (1) "any sub-threshold
    # order" matched routine high-frequency suppliers whose normal weekly volume passively crosses
    # the threshold -- one supplier/requester pair produced a single "window" of 79 ordinary POs,
    # not a split. (2) reporting every row where the rolling sum stayed over threshold produced one
    # exception per subsequent order forever, not one exception per actual split. Fixed with:
    # near_threshold_pct (an order must be a meaningful fraction of the threshold to look like a
    # deliberate piece of a split, not routine small purchasing) and max_pos_in_window (a split is a
    # handful of orders, not dozens); and only the FIRST row where a given window newly crosses the
    # threshold is reported per contiguous run, not every row after it.
    near_threshold = threshold * cfg.get("split_near_threshold_pct", 0.5)
    max_pos = cfg.get("split_max_pos_in_window", 4)

    df = attrs.dropna(subset=["supplier_id", "user_id", "order_value_eur", "start_time"]).copy()
    df = df[(df["order_value_eur"] >= near_threshold) & (df["order_value_eur"] < threshold)]
    if df.empty:
        return _empty()
    df = df.sort_values("start_time")

    rows = []
    for (supplier, user), g in df.groupby(["supplier_id", "user_id"]):
        g = g.set_index("start_time")
        rolling_sum = g["order_value_eur"].rolling(window).sum()
        rolling_n = g["order_value_eur"].rolling(window).count()
        is_hit = (rolling_sum >= threshold) & (rolling_n >= 2) & (rolling_n <= max_pos)
        new_hit = is_hit & ~is_hit.shift(1, fill_value=False)   # only the first row of each run
        for ts, row in g[new_hit].iterrows():
            window_pos = g.loc[(g.index > ts - pd.Timedelta(window)) & (g.index <= ts)]
            rows.append(_exception_row(
                row["case_id"], "C3_threshold_splitting", "high", float(window_pos["order_value_eur"].sum()),
                {"supplier_id": supplier, "user_id": user, "window_days": cfg["split_window_days"],
                 "pos_in_window": window_pos["purchase_order_id"].tolist() if "purchase_order_id" in window_pos else window_pos["case_id"].tolist(),
                 "window_sum_eur": float(window_pos["order_value_eur"].sum()), "threshold_eur": threshold}))
    return pd.DataFrame(rows) if rows else _empty()


# ---------------------------------------------------------------------------
# C4: possible duplicate invoice.
# ---------------------------------------------------------------------------
def check_c4_duplicate_invoice(events: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    acts = cfg["activities"]
    attrs = case_ap_attributes(events)
    inv_time = events[events["activity"] == acts["vendor_invoice"]].groupby("case_id")["timestamp"].min()
    df = attrs.dropna(subset=["supplier_id", "order_value_eur"]).copy()
    df["invoice_time"] = df["case_id"].map(inv_time)
    df = df.dropna(subset=["invoice_time"])
    if len(df) < 2:
        return _empty()

    tol = cfg["duplicate_amount_tolerance_pct"]
    max_gap = pd.Timedelta(days=cfg["duplicate_window_days"])
    max_recurring = cfg.get("duplicate_max_recurring_count", 3)
    rows, seen_pairs = [], set()
    for supplier, g in df.groupby("supplier_id"):
        g = g.sort_values("order_value_eur").reset_index(drop=True)
        n = len(g)
        # A vendor's amount that recurs often across the WHOLE dataset (not just the window) is a
        # routine/subscription charge (a monthly service fee, say), not a candidate duplicate --
        # found necessary running this against real data: fixing the loop bug above surfaced ~12,000
        # pairs, almost all recurring round-number amounts from high-volume service vendors.
        recurring_count = g["order_value_eur"].round(0).map(g["order_value_eur"].round(0).value_counts())
        # Real bug, found running the planted-anomaly evaluation (2026-09-28): the amount-tolerance
        # early-exit was a single `break` inside a flat itertools.combinations(range(n), 2) loop, so
        # ONE out-of-tolerance pair anywhere in the supplier's list aborted checking every remaining
        # pair for every other i, not just the rest of j for that i -- most real duplicate pairs were
        # never reached. Fixed with a proper nested loop: break only ends the inner j scan for the
        # current i (values are sorted ascending, so once b diverges past tolerance from a, larger j
        # only diverge further -- that part of the reasoning was correct, just wired to the wrong loop).
        for i in range(n):
            if recurring_count.iloc[i] > max_recurring:
                continue
            a = g.iloc[i]
            for j in range(i + 1, n):
                if recurring_count.iloc[j] > max_recurring:
                    continue
                b = g.iloc[j]
                if a["case_id"] == b["case_id"]:
                    continue
                denom = max(abs(a["order_value_eur"]), 1e-9)
                if abs(a["order_value_eur"] - b["order_value_eur"]) / denom > tol:
                    break
                if abs(a["invoice_time"] - b["invoice_time"]) > max_gap:
                    continue
                pair = tuple(sorted([a["case_id"], b["case_id"]]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                for c in (a, b):
                    rows.append(_exception_row(c["case_id"], "C4_possible_duplicate_invoice", "medium", c["order_value_eur"],
                                               {"supplier_id": supplier, "matched_case_id": pair[1] if c["case_id"] == pair[0] else pair[0],
                                                "amount_eur": float(c["order_value_eur"]), "tolerance_pct": tol,
                                                "days_apart": abs((a["invoice_time"] - b["invoice_time"]).days)}))
    return pd.DataFrame(rows) if rows else _empty()


# ---------------------------------------------------------------------------
# C5: payment-block override, with a segregation-of-duties flag.
# ---------------------------------------------------------------------------
def check_c5_payment_block_override(events: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    acts = cfg["activities"]
    if acts["remove_payment_block"] not in set(events["activity"]):
        return _empty()
    attrs = case_ap_attributes(events)
    reapproval = [a for a in cfg.get("reapproval_activities", []) if a in set(events["activity"])]
    ev = events.sort_values(["case_id", "timestamp"])

    # Real finding (2026-09-28): the configured reapproval activities DO occur in this dataset, but
    # almost never AFTER a block removal -- "Record Invoice Receipt" normally happens BEFORE the
    # payment block is removed, not after, so it was never really a post-override reapproval step
    # here; it just happened not to occur in the window by construction of the process, which made
    # the "no reapproval" condition true for ~97% of override cases regardless of the config. Fixed
    # by measuring, empirically, what fraction of block-removal cases have ANY configured activity
    # occurring anytime after the block removal (not required to precede the clear) -- if that's
    # low, these activities don't function as a post-block reapproval step in this system's logged
    # process at all, and severity is downgraded rather than reporting a false compliance failure.
    block_cases = events.loc[events["activity"] == acts["remove_payment_block"]].groupby("case_id")["timestamp"].min()
    if len(reapproval) and len(block_cases):
        after_block = events[events["case_id"].isin(block_cases.index) & events["activity"].isin(reapproval)]
        after_block = after_block[after_block["timestamp"] > after_block["case_id"].map(block_cases)]
        reapproval_functions_post_block = after_block["case_id"].nunique() / len(block_cases)
    else:
        reapproval_functions_post_block = 0.0
    config_activities_exist = len(reapproval) > 0 and reapproval_functions_post_block >= 0.15
    rows = []
    for case_id, g in ev.groupby("case_id"):
        block_times = g.loc[g["activity"] == acts["remove_payment_block"], "timestamp"]
        clear_times = g.loc[g["activity"] == acts["clear_invoice"], "timestamp"]
        if block_times.empty or clear_times.empty:
            continue
        block_t = block_times.min()
        clear_after = clear_times[clear_times > block_t]
        if clear_after.empty:
            continue
        clear_t = clear_after.min()
        between = g[(g["timestamp"] > block_t) & (g["timestamp"] < clear_t)]
        had_reapproval = config_activities_exist and between["activity"].isin(reapproval).any()
        if had_reapproval:
            continue
        exposure = attrs.loc[attrs["case_id"] == case_id, "order_value_eur"]
        exposure = float(exposure.iloc[0]) if len(exposure) and pd.notna(exposure.iloc[0]) else None
        severity = "medium" if config_activities_exist else "low"

        po_user = g["user_id"].iloc[0] if "user_id" in g.columns and len(g) else None
        clear_row = g[g["timestamp"] == clear_t]
        clear_user = clear_row["user_id"].iloc[0] if "user_id" in clear_row.columns and len(clear_row) else None
        sod_violation = bool(po_user) and bool(clear_user) and po_user == clear_user and po_user != "UNKNOWN"

        rows.append(_exception_row(case_id, "C5_payment_block_override", "high" if sod_violation else severity, exposure,
                                   {"block_removed_at": str(block_t), "invoice_cleared_at": str(clear_t),
                                    "reapproval_activities_configured": cfg.get("reapproval_activities", []),
                                    "reapproval_activities_function_as_post_block_step": config_activities_exist,
                                    "reapproval_activity_post_block_rate": round(reapproval_functions_post_block, 4),
                                    "segregation_of_duties_violation": sod_violation,
                                    "po_creator_user_id": po_user, "invoice_clearer_user_id": clear_user}))
    return pd.DataFrame(rows) if rows else _empty()


# ---------------------------------------------------------------------------
# C6: Benford's law screen on invoice amounts per vendor.
# ---------------------------------------------------------------------------
_BENFORD = np.array([np.log10(1 + 1 / d) for d in range(1, 10)])


def _leading_digit(x: float) -> int:
    x = abs(x)
    while x >= 10:
        x /= 10
    while 0 < x < 1:
        x *= 10
    return int(x) if x >= 1 else 0


def check_c6_benford(events: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Screening signal only, per docs/ap-controls.md: Benford deviation is common in real,
    legitimate data too (price points, contract caps) and is never itself evidence of anything."""
    from scipy import stats

    attrs = case_ap_attributes(events)
    df = attrs.dropna(subset=["supplier_id", "order_value_eur"])
    df = df[df["order_value_eur"] > 0]
    min_n = cfg["benford_min_invoices_per_vendor"]
    rows = []
    for supplier, g in df.groupby("supplier_id"):
        if len(g) < min_n:
            continue
        digits = g["order_value_eur"].map(_leading_digit)
        digits = digits[digits.between(1, 9)]
        if len(digits) < min_n:
            continue
        observed = digits.value_counts().reindex(range(1, 10), fill_value=0).values
        expected = _BENFORD * observed.sum()
        chi2, p = stats.chisquare(observed, expected)
        if p < cfg["benford_alert_p_value"]:
            rows.append(_exception_row(None, "C6_benford_deviation", "low", float(g["order_value_eur"].sum()),
                                       {"supplier_id": supplier, "n_invoices": int(len(digits)),
                                        "chi2": round(float(chi2), 2), "p_value": round(float(p), 4),
                                        "observed_leading_digit_counts": observed.tolist()}))
    return pd.DataFrame(rows) if rows else _empty()


ALL_CHECKS = {
    "C1_three_way_match": check_c1_three_way_match,
    "C2_invoice_gr_order_mismatch": check_c2_invoice_gr_order,
    "C3_threshold_splitting": check_c3_threshold_splitting,
    "C4_possible_duplicate_invoice": check_c4_duplicate_invoice,
    "C5_payment_block_override": check_c5_payment_block_override,
    "C6_benford_deviation": check_c6_benford,
}


def run_all_controls(events: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    frames = [fn(events, cfg) for fn in ALL_CHECKS.values()]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return _empty()
    return pd.concat(frames, ignore_index=True)
