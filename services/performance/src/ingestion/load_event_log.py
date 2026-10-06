from pathlib import Path
import pandas as pd

RAW_COLUMN_MAP = {
    "case:concept:name": "case_id",
    "concept:name": "activity",
    "time:timestamp": "timestamp",
    "org:resource": "resource",
    "case:Purchasing Document": "purchase_order_id",
    "case:Item": "item_id",
    "case:Spend area text": "category",
    "case:Vendor": "supplier_id",
    # AP-control fields (2026-09-28, finance module Phase F1) -- real BPI 2019 attributes,
    # confirmed present in the source XES; see docs/data-contract.md and
    # scripts/download_bpi2019_sample.py's docstring for how they were verified and sampled.
    "case:Item Category": "item_category",          # e.g. "3-way match, invoice before GR"
    "case:GR-Based Inv. Verif.": "gr_based_inv_verif",  # source string "true"/"false"
    "case:Goods Receipt": "goods_receipt_required",     # source string "true"/"false"
    "case:Document Type": "document_type",
    "case:Item Type": "item_type",
    "case:Company": "company",
    "case:Sub spend area text": "sub_spend_area",
    "User": "user_id",                               # event-level actor (distinct from org:resource)
    "Cumulative net worth (EUR)": "net_worth_eur",
}

REQUIRED_RAW_COLUMNS = [
    "case:concept:name",
    "concept:name",
    "time:timestamp",
]

_STRING_ID_COLS = ("purchase_order_id", "item_id")
_BOOLEAN_COLS = ("gr_based_inv_verif", "goods_receipt_required")
_NUMERIC_COLS = ("net_worth_eur",)


def load_event_log(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Event log not found at {path}")

    df = pd.read_csv(path, low_memory=False)

    missing = [c for c in REQUIRED_RAW_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Event log is missing required columns: {missing}")

    present_map = {k: v for k, v in RAW_COLUMN_MAP.items() if k in df.columns}
    df = df.rename(columns=present_map)

    keep = [v for v in present_map.values() if v in df.columns]
    df = df[keep].copy()

    # purchase_order_id/item_id are identifiers, not quantities -- but a real source CSV
    # with bare-digit values (e.g. this project's BPI 2019 sample, whose Purchasing
    # Document/Item fields are numeric-looking) gets them inferred as int64 by
    # pd.read_csv, which the data contract (correctly) rejects as "not string-like".
    # Cast explicitly rather than relaxing the contract, since the contract's
    # expectation is the correct one -- an ID should never silently become a number.
    for id_col in _STRING_ID_COLS:
        if id_col in df.columns:
            df[id_col] = df[id_col].astype("string")

    # Source booleans are the literal strings "true"/"false" (XES <boolean value="true">,
    # written through untouched by the CSV sampler) -- normalize to real bool, NaN if absent.
    for col in _BOOLEAN_COLS:
        if col in df.columns:
            df[col] = df[col].map({"true": True, "false": False, True: True, False: False})

    # net_worth_eur is per-event cumulative net worth -- blank for events that don't carry it
    # (not every activity reports a running total); coerce rather than drop the column.
    for col in _NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    events = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    print(f"Loaded {len(events):,} rows, {events['case_id'].nunique():,} cases")
    print(events.head())
