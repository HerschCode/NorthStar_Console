"""External validation of the AP controls on two public, non-BPI datasets.

Run: PYTHONPATH=. python -m scripts.external_validation   (raw data under data/external/, git-ignored;
fetch with the URLs in docs/external-validation.md) -> reports/external_validation.json

1. UCI Online Retail II (real UK retailer invoices 2009-11). Mapped to the AP schema: invoice -> case,
   Customer ID -> supplier_id and user_id (the counterparty), invoice total (qty*price, GBP, no conversion) ->
   order value. C3/C4/C6 run unchanged. Real "credit note" invoices (prefix C) give a label PROXY for C4:
   a flagged invoice that was later reversed by a credit note from the same customer for ~the same amount.
2. SEC EDGAR Financial Statement Data Sets 2024q1: Benford first-digit test on reported USD values, per
   filer, as a BASE-RATE check of C6 on ordinary real data (a screen should not fire on most of it).
No thresholds were tuned for these data; config/ap_controls.yaml is used as is.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from src.controls.ap_controls import (_BENFORD, _leading_digit, check_c3_threshold_splitting,
                                      check_c4_duplicate_invoice, check_c6_benford, load_config)

EXT = Path("data/external")


def retail_events() -> tuple[pd.DataFrame, pd.DataFrame]:
    z = zipfile.ZipFile(EXT / "online_retail_ii.zip")
    sheets = pd.read_excel(z.open("online_retail_II.xlsx"), sheet_name=None)
    df = pd.concat(sheets.values(), ignore_index=True).dropna(subset=["Customer ID"])
    df["Invoice"] = df["Invoice"].astype(str)
    df["amount"] = df["Quantity"] * df["Price"]
    inv = df.groupby("Invoice").agg(cust=("Customer ID", "first"), ts=("InvoiceDate", "min"),
                                    amount=("amount", "sum")).reset_index()
    credit = inv[inv["Invoice"].str.startswith("C")].copy()
    sale = inv[~inv["Invoice"].str.startswith("C") & (inv["amount"] > 0)].copy()
    ev = pd.DataFrame({"case_id": sale["Invoice"], "activity": "Vendor creates invoice", "timestamp": sale["ts"],
                       "supplier_id": sale["cust"].astype(int).astype(str), "user_id": sale["cust"].astype(int).astype(str),
                       "net_worth_eur": sale["amount"]})
    return ev, credit


def reversed_proxy(sale_ids: set, ev: pd.DataFrame, credit: pd.DataFrame, tol=0.01, days=60) -> set:
    """Sale invoices reversed by a credit note (same customer, |amount| within tol, within `days` after)."""
    out = set()
    cs = credit.assign(cust=credit["cust"].astype(int).astype(str)).groupby("cust")
    for _, r in ev[ev["case_id"].isin(sale_ids)].iterrows():
        if r["supplier_id"] not in cs.groups:
            continue
        c = cs.get_group(r["supplier_id"])
        m = (abs(abs(c["amount"]) - r["net_worth_eur"]) <= tol * r["net_worth_eur"]) & \
            (c["ts"] >= r["timestamp"]) & (c["ts"] <= r["timestamp"] + pd.Timedelta(days=days))
        if m.any():
            out.add(r["case_id"])
    return out


def benford_stats(values: np.ndarray) -> dict:
    d = np.array([_leading_digit(v) for v in values])
    d = d[(d >= 1) & (d <= 9)]
    obs = np.bincount(d, minlength=10)[1:]
    n = int(obs.sum())
    chi2, p = stats.chisquare(obs, _BENFORD * n)
    mad = float(np.mean(np.abs(obs / n - _BENFORD)))
    return {"n": n, "chi2_p": float(p), "mad": mad}


def main():
    cfg = load_config()
    res = {}

    ev, credit = retail_events()
    sale_amt = ev["net_worth_eur"]
    res["retail"] = {"invoices": int(len(ev)), "customers": int(ev["supplier_id"].nunique()),
                     "credit_notes": int(len(credit)), "invoice_total_gbp_quantiles":
                     {q: round(float(sale_amt.quantile(q)), 2) for q in (0.5, 0.9, 0.99, 0.999)},
                     "invoices_over_threshold": int((sale_amt >= cfg["approval_threshold_eur"]).sum())}
    all_rev = reversed_proxy(set(ev["case_id"]), ev, credit)
    base = len(all_rev) / len(ev)
    res["retail"]["base_rate_reversed"] = round(base, 4)

    c4 = check_c4_duplicate_invoice(ev, cfg)
    flagged = set(c4["case_id"]) if not c4.empty else set()
    fl_rev = flagged & all_rev
    res["retail"]["C4"] = {"flagged_invoices": len(flagged), "flag_rate": round(len(flagged) / len(ev), 4),
                           "flagged_later_reversed": len(fl_rev),
                           "precision_vs_reversal_proxy": round(len(fl_rev) / len(flagged), 4) if flagged else None,
                           "lift_over_base": round(len(fl_rev) / len(flagged) / base, 2) if flagged and base else None,
                           "note": "reversal proxy is not a duplicate label: customers return goods for many reasons"}
    c3 = check_c3_threshold_splitting(ev, cfg)
    res["retail"]["C3"] = {"flagged": int(len(c3)), "note": "threshold 10,000 applied to GBP totals unchanged"}
    c6 = check_c6_benford(ev, cfg)
    elig = ev.groupby("supplier_id").size()
    n_elig = int((elig >= cfg["benford_min_invoices_per_vendor"]).sum())
    res["retail"]["C6"] = {"eligible_customers": n_elig, "flagged": int(len(c6)),
                           "flag_rate_among_eligible": round(len(c6) / n_elig, 4) if n_elig else None,
                           "null_expectation": cfg["benford_alert_p_value"]}
    res["retail"]["benford_all_invoices"] = benford_stats(sale_amt.values)

    z = zipfile.ZipFile(EXT / "sec_2024q1.zip")
    num = pd.read_csv(z.open("num.txt"), sep="\t", usecols=["adsh", "uom", "value"], low_memory=False)
    num = num[(num["uom"] == "USD") & (num["value"] > 0)].dropna()
    res["sec"] = {"usd_values": int(len(num)), "filers": int(num["adsh"].nunique()),
                  "benford_all": benford_stats(num["value"].values)}
    per = []
    for adsh, g in num.groupby("adsh"):
        if len(g) >= cfg["benford_min_invoices_per_vendor"]:
            per.append(benford_stats(g["value"].values) | {"adsh": adsh})
    per = pd.DataFrame(per)
    pv = per["chi2_p"].values
    res["sec"]["per_filing"] = {
        "eligible_filings": int(len(per)), "flag_rate_p_lt_0.05": round(float((pv < 0.05).mean()), 4),
        "null_expectation": 0.05, "median_n": int(per["n"].median()),
        "median_mad": round(float(per["mad"].median()), 4),
        "flag_rate_p_lt_0.05_and_mad_gt_0.015": round(float(((pv < 0.05) & (per["mad"] > 0.015)).mean()), 4),
        "flag_rate_by_n": {lab: round(float((g["chi2_p"] < 0.05).mean()), 4) for lab, g in
                           per.groupby(pd.cut(per["n"], [29, 100, 500, 2000, 1e9], labels=["30-100", "101-500", "501-2000", ">2000"]),
                                       observed=True)}}
    Path("reports/external_validation.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
