"""Phase F3: measure C1 (three-way-match), C3 (threshold splitting) and C4 (duplicate invoice)
honestly -- BPI 2019 has no fraud/anomaly labels, so nothing here is validated against real ground
truth. Instead: plant synthetic anomalies at a KNOWN, seeded rate into a COPY of the real data, run
the controls, and report recall / false-positive rate against the planted set. The real (unplanted)
exception counts from scripts/run_ap_controls.py are NEVER mixed into these numbers -- a real
exception here would be counted as a "false positive" by construction (there is no way to know if
it's really anomalous), which is disclosed explicitly, not glossed over.

Also compares an unsupervised Isolation Forest (case-level numeric features) against the rule
exceptions on the SAME planted anomalies: what each approach catches that the other misses.

C2 (order mismatch) and C5 (payment-block override) are not evaluated here: they are structural/
label-comparison checks with no plausible "plant a synthetic instance" procedure (see
docs/ap-controls.md) -- their real counts are reported as-is, disclosed as unlabeled.

Run: python -m scripts.evaluate_ap_controls  -> reports/ap_controls_evaluation.json
"""
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.ensemble import IsolationForest

from src.cleaning.clean_events import clean_events
from src.controls.ap_controls import (
    check_c1_three_way_match, check_c3_threshold_splitting, check_c4_duplicate_invoice, load_config)
from src.controls.case_attributes import case_ap_attributes
from src.ingestion.load_event_log import load_event_log

warnings.filterwarnings("ignore")
SEED = 0
N_PLANTED = 40           # per control


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z ** 2 / n
    center = (p + z ** 2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def plant_c1(events: pd.DataFrame, cfg: dict, rng: np.random.Generator, n: int) -> tuple[pd.DataFrame, list]:
    """Remove the goods-receipt event from n real 3-way-match cases that currently HAVE one."""
    acts = cfg["activities"]
    attrs = case_ap_attributes(events)
    three_way = set(attrs.loc[attrs["item_category"].fillna("").str.contains("3-way", case=False), "case_id"])
    has_gr = set(events.loc[events["activity"] == acts["goods_receipt"], "case_id"])
    candidates = sorted(three_way & has_gr)
    if len(candidates) < n:
        n = len(candidates)
    picked = rng.choice(candidates, size=n, replace=False).tolist()
    out = events[~((events["case_id"].isin(picked)) & (events["activity"] == acts["goods_receipt"]))]
    return out, picked


def plant_c3(events: pd.DataFrame, cfg: dict, rng: np.random.Generator, n: int) -> tuple[pd.DataFrame, list]:
    """Add n synthetic 3-piece splits: 3 sub-threshold POs, same supplier+user, within the window,
    summing just over the threshold."""
    threshold = cfg["approval_threshold_eur"]
    real_suppliers = events["supplier_id"].dropna().unique()
    real_times = pd.to_datetime(events["timestamp"]).dropna()
    base_time = real_times.quantile(0.5)   # median, not min -- BPI 2019 has a few known-anomalous
                                            # early timestamps (docs/data-contract.md); anchoring to
                                            # the min would place every planted case in the same
                                            # implausible outlier period instead of a realistic one
    new_rows, planted_ids = [], []
    for i in range(n):
        case_ids = [f"SYN_C3_{i}_{j}" for j in range(3)]
        supplier = rng.choice(real_suppliers)
        user = f"SYN_USER_{i}"
        piece = threshold * 0.6   # must clear split_near_threshold_pct (0.5) to be a plausible split piece
        start = base_time + pd.Timedelta(days=int(rng.integers(0, 300)))
        for j, cid in enumerate(case_ids):
            new_rows.append({"case_id": cid, "activity": cfg["activities"]["create_po"],
                             "timestamp": start + pd.Timedelta(days=j), "supplier_id": supplier,
                             "user_id": user, "net_worth_eur": piece, "purchase_order_id": cid,
                             "item_category": "Consignment"})
        planted_ids.extend(case_ids)
    return pd.concat([events, pd.DataFrame(new_rows)], ignore_index=True), planted_ids


def plant_c4(events: pd.DataFrame, cfg: dict, rng: np.random.Generator, n: int) -> tuple[pd.DataFrame, list]:
    """Clone n real invoices with a near-identical amount 1-2 days later, new case id."""
    acts = cfg["activities"]
    inv = events[events["activity"] == acts["vendor_invoice"]]
    attrs = case_ap_attributes(events)
    candidates = attrs.dropna(subset=["supplier_id", "order_value_eur"])
    candidates = candidates[candidates["case_id"].isin(inv["case_id"])]
    if len(candidates) < n:
        n = len(candidates)
    picked = candidates.sample(n=n, random_state=int(rng.integers(0, 2**31))).reset_index(drop=True)
    new_rows, planted_ids = [], []
    for i, row in picked.iterrows():
        t = inv.loc[inv["case_id"] == row["case_id"], "timestamp"].min() + pd.Timedelta(days=1)
        dup_id = f"SYN_C4_{i}"
        amt = row["order_value_eur"] * (1 + rng.uniform(-0.005, 0.005))
        new_rows.append({"case_id": dup_id, "activity": acts["vendor_invoice"], "timestamp": t,
                         "supplier_id": row["supplier_id"], "net_worth_eur": amt, "purchase_order_id": dup_id})
        planted_ids.append(dup_id)
    return pd.concat([events, pd.DataFrame(new_rows)], ignore_index=True), planted_ids


def evaluate(name, plant_fn, check_fn, events, cfg, rng, n=N_PLANTED, evidence_credit_key=None):
    """evidence_credit_key: for controls (C3) that report one exception per GROUP rather than per
    case, a planted case also counts as caught if it appears in the flagging exception's evidence
    under this key (e.g. "pos_in_window") -- otherwise recall would undercount by design, not by
    a detection failure."""
    augmented, planted = plant_fn(events, cfg, rng, n)
    exceptions = check_fn(augmented, cfg)
    flagged = set(exceptions["case_id"].dropna())          # cases with their OWN exception row
    credited = set(flagged)                                 # + cases only reachable via evidence (recall only)
    if evidence_credit_key and len(exceptions):
        for ev in exceptions["evidence"]:
            credited |= set(ev.get(evidence_credit_key, []))
    caught = [c for c in planted if c in credited]
    real_case_ids = set(events["case_id"].unique())
    false_positives = flagged - set(planted)   # false positives = real cases with their OWN exception row
    false_positives &= real_case_ids
    recall = len(caught) / len(planted) if planted else None
    fpr = len(false_positives) / len(real_case_ids) if real_case_ids else None
    lo, hi = wilson_ci(len(caught), len(planted)) if planted else (None, None)
    return {"control": name, "n_planted": len(planted), "n_caught": len(caught),
            "recall": round(recall, 4) if recall is not None else None,
            "recall_ci95": [round(lo, 4), round(hi, 4)] if lo is not None else None,
            "n_false_positives_among_real_cases": len(false_positives),
            "false_positive_rate": round(fpr, 4) if fpr is not None else None,
            "note": "false_positive_rate counts REAL cases flagged by this run as anomalous; a real case flagged here "
                    "may or may not be a genuine issue -- there is no ground truth to tell, so this is an upper bound "
                    "on the false-positive rate, not a confirmed one."}


def isolation_forest_comparison(events: pd.DataFrame, cfg: dict, rng: np.random.Generator,
                                planted_c1: list, planted_c3: list, planted_c4: list) -> dict:
    """Case-level numeric features -> IsolationForest anomaly score; compare its top-K flagged
    cases against the union of all planted anomalies (same K as the total planted count)."""
    from src.transformation.build_process_cases import build_process_cases

    all_planted = set(planted_c1) | set(planted_c3) | set(planted_c4)
    cases = build_process_cases(events)
    attrs = case_ap_attributes(events)
    df = cases.merge(attrs[["case_id", "order_value_eur"]], on="case_id", how="left")
    feats = df[["event_count", "unique_activity_count", "rework_count", "cycle_time_hours", "order_value_eur"]].fillna(0)
    model = IsolationForest(n_estimators=200, random_state=int(rng.integers(0, 2**31)), contamination="auto")
    scores = -model.fit(feats).score_samples(feats)   # higher = more anomalous
    df["anomaly_score"] = scores
    k = len(all_planted)
    top_k = set(df.nlargest(k, "anomaly_score")["case_id"])
    caught = top_k & all_planted
    return {"k": k, "n_planted_total": len(all_planted), "n_caught_by_isolation_forest": len(caught),
            "recall": round(len(caught) / len(all_planted), 4) if all_planted else None,
            "caught_case_ids": sorted(caught)}


def main():
    load_dotenv()
    import os

    events = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(events)
    cfg = load_config()
    rng = np.random.default_rng(SEED)

    results = {
        "seed": SEED, "n_planted_per_control": N_PLANTED,
        "label": "PLANTED-ANOMALY EVALUATION: recall/FPR measured against synthetic injections only. "
                "Real exception counts (scripts/run_ap_controls.py) are separate and unlabeled -- "
                "never mixed into these numbers.",
        "controls": {},
    }

    aug_c1, planted_c1 = plant_c1(cleaned, cfg, rng, N_PLANTED)
    results["controls"]["C1_three_way_match"] = evaluate("C1_three_way_match", plant_c1, check_c1_three_way_match, cleaned, cfg, rng)
    aug_c3, planted_c3 = plant_c3(cleaned, cfg, rng, N_PLANTED)
    results["controls"]["C3_threshold_splitting"] = evaluate("C3_threshold_splitting", plant_c3, check_c3_threshold_splitting, cleaned, cfg, rng,
                                                              evidence_credit_key="pos_in_window")
    aug_c4, planted_c4 = plant_c4(cleaned, cfg, rng, N_PLANTED)
    results["controls"]["C4_possible_duplicate_invoice"] = evaluate("C4_possible_duplicate_invoice", plant_c4, check_c4_duplicate_invoice, cleaned, cfg, rng)

    results["isolation_forest_vs_rules"] = isolation_forest_comparison(cleaned, cfg, rng, planted_c1, planted_c3, planted_c4)
    results["not_evaluated"] = {
        "C2_invoice_gr_order_mismatch": "structural label-comparison check (actual order vs item_category's stated order) -- no plausible synthetic-plant procedure; real count reported as-is, unlabeled",
        "C5_payment_block_override": "same reason; a 'planted override' would just be another Remove Payment Block event, indistinguishable from a real one",
        "C6_benford_deviation": "a screening signal over amount distributions, not a per-case detector -- recall/FPR at the case level doesn't apply",
    }

    Path("reports").mkdir(exist_ok=True)
    Path("reports/ap_controls_evaluation.json").write_text(json.dumps(results, indent=2))
    for name, r in results["controls"].items():
        print(f"{name}: recall {r['recall']} {r['recall_ci95']} ({r['n_caught']}/{r['n_planted']}), "
              f"FPR (upper bound) {r['false_positive_rate']} ({r['n_false_positives_among_real_cases']} real cases)")
    print("Isolation Forest:", results["isolation_forest_vs_rules"])
    print("wrote reports/ap_controls_evaluation.json")


if __name__ == "__main__":
    main()
