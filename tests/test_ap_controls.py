"""Hand-built positive and negative cases per AP control (docs/ap-controls.md's acceptance
criteria). Every case is a small, readable events DataFrame -- what actually happened -- checked
against what the control should conclude, not against the model's own output."""
import pandas as pd
import pytest

from src.controls.ap_controls import (
    check_c1_three_way_match, check_c2_invoice_gr_order, check_c3_threshold_splitting,
    check_c4_duplicate_invoice, check_c5_payment_block_override, check_c6_benford, load_config)

CFG = load_config()
A = CFG["activities"]


def _events(rows):
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for col in ("supplier_id", "user_id", "item_category", "purchase_order_id"):
        if col not in df.columns:
            df[col] = None
    if "net_worth_eur" not in df.columns:
        df["net_worth_eur"] = None
    return df


def _ev(case_id, activity, day, **kw):
    return {"case_id": case_id, "activity": activity, "timestamp": f"2024-01-{day:02d}", **kw}


# ---------------------------------------------------------------------------- C1
def test_c1_flags_three_way_match_invoice_with_no_goods_receipt():
    events = _events([
        _ev("bad1", A["create_po"], 1, item_category="3-way match, invoice after GR"),
        _ev("bad1", A["vendor_invoice"], 3, item_category="3-way match, invoice after GR"),
        _ev("good1", A["create_po"], 1, item_category="3-way match, invoice after GR"),
        _ev("good1", A["goods_receipt"], 2, item_category="3-way match, invoice after GR"),
        _ev("good1", A["vendor_invoice"], 3, item_category="3-way match, invoice after GR"),
        _ev("other1", A["create_po"], 1, item_category="2-way match"),
        _ev("other1", A["vendor_invoice"], 2, item_category="2-way match"),
    ])
    out = check_c1_three_way_match(events, CFG)
    assert list(out["case_id"]) == ["bad1"]


# ---------------------------------------------------------------------------- C2
def test_c2_flags_wrong_order_and_passes_correct_order():
    events = _events([
        # labelled "before GR" but goods receipt actually happens first -> violation
        _ev("bad2", A["goods_receipt"], 1, item_category="3-way match, invoice before GR"),
        _ev("bad2", A["vendor_invoice"], 3, item_category="3-way match, invoice before GR"),
        # labelled "after GR" and goods receipt does come first -> conforms
        _ev("good2", A["goods_receipt"], 1, item_category="3-way match, invoice after GR"),
        _ev("good2", A["vendor_invoice"], 3, item_category="3-way match, invoice after GR"),
    ])
    out = check_c2_invoice_gr_order(events, CFG)
    assert list(out["case_id"]) == ["bad2"]


# ---------------------------------------------------------------------------- C3
def test_c3_flags_split_purchases_summing_over_threshold():
    thr = CFG["approval_threshold_eur"]
    events = _events([
        _ev("s1", A["create_po"], 1, supplier_id="V1", user_id="U1", net_worth_eur=thr * 0.6),
        _ev("s2", A["create_po"], 2, supplier_id="V1", user_id="U1", net_worth_eur=thr * 0.6),
        _ev("s3", A["create_po"], 3, supplier_id="V1", user_id="U1", net_worth_eur=thr * 0.6),
        # different user -- should not combine with the V1/U1 group even though same supplier
        _ev("clean1", A["create_po"], 1, supplier_id="V1", user_id="U2", net_worth_eur=thr * 0.9),
        # single order well under threshold, no other orders nearby -- not a split
        _ev("clean2", A["create_po"], 1, supplier_id="V2", user_id="U3", net_worth_eur=thr * 0.1),
    ])
    out = check_c3_threshold_splitting(events, CFG)
    assert set(out["case_id"]) <= {"s1", "s2", "s3"}
    assert len(out) >= 1
    assert "clean1" not in set(out["case_id"]) and "clean2" not in set(out["case_id"])


def test_c3_does_not_flag_routine_high_frequency_small_purchasing():
    """Regression (2026-09-28): a supplier/requester with many ordinary small POs crossed the
    rolling-sum threshold by volume alone -- 3,288 of 9,228 real cases flagged before this fix."""
    thr = CFG["approval_threshold_eur"]
    events = _events([_ev(f"routine{i}", A["create_po"], (i % 27) + 1, supplier_id="V9", user_id="U9",
                          net_worth_eur=thr * 0.05) for i in range(60)])
    assert check_c3_threshold_splitting(events, CFG).empty


def test_c3_flags_only_once_per_split_not_once_per_subsequent_order():
    thr = CFG["approval_threshold_eur"]
    events = _events([
        _ev("run1", A["create_po"], 1, supplier_id="V8", user_id="U8", net_worth_eur=thr * 0.6),
        _ev("run2", A["create_po"], 2, supplier_id="V8", user_id="U8", net_worth_eur=thr * 0.6),
        _ev("run3", A["create_po"], 3, supplier_id="V8", user_id="U8", net_worth_eur=thr * 0.6),
    ])
    out = check_c3_threshold_splitting(events, CFG)
    assert len(out) == 1


def test_c3_does_not_flag_purchases_outside_the_window():
    thr = CFG["approval_threshold_eur"]
    events = _events([
        _ev("far1", A["create_po"], 1, supplier_id="V3", user_id="U4", net_worth_eur=thr * 0.6),
        {"case_id": "far2", "activity": A["create_po"], "timestamp": "2024-03-01",
         "supplier_id": "V3", "user_id": "U4", "net_worth_eur": thr * 0.6},
    ])
    out = check_c3_threshold_splitting(events, CFG)
    assert out.empty


# ---------------------------------------------------------------------------- C4
def test_c4_flags_close_duplicate_and_ignores_distant_or_dissimilar_invoices():
    events = _events([
        _ev("d1", A["vendor_invoice"], 1, supplier_id="V5", net_worth_eur=5000),
        _ev("d2", A["vendor_invoice"], 2, supplier_id="V5", net_worth_eur=5010),      # near-duplicate, 1 day apart
        _ev("d3", A["vendor_invoice"], 25, supplier_id="V5", net_worth_eur=5005),     # same amount, far apart in time
        _ev("d4", A["vendor_invoice"], 1, supplier_id="V5", net_worth_eur=9000),      # different amount
    ])
    out = check_c4_duplicate_invoice(events, CFG)
    flagged = set(out["case_id"])
    assert {"d1", "d2"} <= flagged
    assert "d3" not in flagged and "d4" not in flagged


# ---------------------------------------------------------------------------- C5
def test_c5_flags_override_without_reapproval_and_segregation_of_duties():
    events = _events([
        _ev("ov1", A["create_po"], 1, user_id="ALICE"),
        _ev("ov1", A["remove_payment_block"], 2, user_id="ALICE"),
        _ev("ov1", A["clear_invoice"], 3, user_id="ALICE"),   # same user created PO and cleared invoice
        _ev("ok1", A["create_po"], 1, user_id="BOB"),
        _ev("ok1", A["remove_payment_block"], 2, user_id="BOB"),
        _ev("ok1", A["invoice_receipt"], 3, user_id="CAROL"),  # a configured reapproval activity, in between
        _ev("ok1", A["clear_invoice"], 4, user_id="DAVE"),
    ])
    out = check_c5_payment_block_override(events, CFG)
    ov = out[out["case_id"] == "ov1"].iloc[0]
    assert ov["evidence"]["segregation_of_duties_violation"] is True
    assert ov["severity"] == "high"
    assert "ok1" not in set(out["case_id"])


def test_c5_downgrades_severity_when_no_configured_reapproval_activity_exists_in_the_dataset():
    cfg = {**CFG, "reapproval_activities": ["This Activity Does Not Exist"]}
    events = _events([
        _ev("ov2", A["create_po"], 1, user_id="ALICE"),
        _ev("ov2", A["remove_payment_block"], 2, user_id="ALICE"),
        _ev("ov2", A["clear_invoice"], 3, user_id="ZOE"),
    ])
    out = check_c5_payment_block_override(events, cfg)
    row = out[out["case_id"] == "ov2"].iloc[0]
    assert row["severity"] == "low"
    assert row["evidence"]["reapproval_activities_function_as_post_block_step"] is False


# ---------------------------------------------------------------------------- C6
def test_c6_flags_a_vendor_whose_amounts_are_all_the_same_leading_digit():
    import numpy as np
    rng = np.random.default_rng(0)
    rows = []
    for i in range(40):
        rows.append(_ev(f"benf{i}", A["create_po"], (i % 27) + 1, supplier_id="SUSPECT",
                        net_worth_eur=1000 + rng.integers(0, 900)))  # every amount starts with "1"
    for i in range(40):
        # natural (Benford-consistent) amounts: exponential magnitudes -> mixed leading digits
        rows.append(_ev(f"clean{i}", A["create_po"], (i % 27) + 1, supplier_id="NATURAL",
                        net_worth_eur=float(10 ** rng.uniform(1, 5))))
    events = _events(rows)
    out = check_c6_benford(events, CFG)
    flagged = {r["supplier_id"] for r in out["evidence"]}
    assert "SUSPECT" in flagged


def test_c6_skips_vendors_below_the_minimum_invoice_count():
    events = _events([_ev(f"few{i}", A["create_po"], i + 1, supplier_id="SMALL", net_worth_eur=1000 + i)
                      for i in range(5)])
    assert check_c6_benford(events, CFG).empty


def test_c4_does_not_flag_a_vendors_routine_recurring_amount():
    """Regression (2026-09-28): fixing the loop-scope bug above surfaced ~12,000 pairs on real data,
    almost all a vendor's routine recurring amount (e.g. a monthly service fee) invoiced repeatedly."""
    events = _events([_ev(f"rec{i}", A["vendor_invoice"], (i % 27) + 1, supplier_id="V6", net_worth_eur=250.0)
                      for i in range(10)])
    assert check_c4_duplicate_invoice(events, CFG).empty


def test_c4_still_flags_a_rare_amount_duplicated_amid_a_vendors_routine_invoices():
    rows = [_ev(f"rec{i}", A["vendor_invoice"], (i % 27) + 1, supplier_id="V7", net_worth_eur=250.0)
           for i in range(10)]
    rows += [_ev("rare1", A["vendor_invoice"], 1, supplier_id="V7", net_worth_eur=8123.0),
             _ev("rare2", A["vendor_invoice"], 2, supplier_id="V7", net_worth_eur=8125.0)]
    out = check_c4_duplicate_invoice(_events(rows), CFG)
    assert {"rare1", "rare2"} <= set(out["case_id"])
