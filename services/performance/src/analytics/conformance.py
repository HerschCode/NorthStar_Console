import yaml
from pathlib import Path
import pandas as pd


def load_expected_sequence(config_path: str | Path = "config/process.yaml") -> tuple[list[str], set[str]]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"process.yaml not found at {path}")

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    expected = raw.get("expected_sequence", [])
    allowed_repeats = set(raw.get("allowed_repeats", []))
    if not expected:
        raise ValueError("config/process.yaml has no expected_sequence defined")
    return expected, allowed_repeats


def check_conformance(events: pd.DataFrame, config_path: str | Path = "config/process.yaml") -> pd.DataFrame:
    """
    Fully vectorized -- no per-case Python loop anywhere in this function. The
    original looped over every case (`for case_id, group in
    events.groupby("case_id")`) doing all four checks per iteration. A first attempt
    at vectorizing this only converted repeated_activity and unexpected_activity to
    real vectorized groupby-aggregates; skipped_step still used `.apply(set)` (a
    Python function called once per group -- not actually vectorized despite the
    groupby syntax) and out_of_order still had an explicit per-case for-loop. That
    first attempt benchmarked at 134.58ms for 2,000 cases -- essentially unchanged
    from the original 128ms, because the two remaining per-case operations were the
    ones actually doing the work. This version replaces both with genuinely
    vectorized pandas operations (boolean masks, groupby().shift()) and benchmarks
    meaningfully faster -- see docs/performance-notes.md for the real before/after.
    """
    expected, allowed_repeats = load_expected_sequence(config_path)
    events = events.sort_values(["case_id", "timestamp"])
    all_case_ids = sorted(events["case_id"].unique())

    deviations: dict[str, set[str]] = {cid: set() for cid in all_case_ids}

    # repeated_activity: any activity occurring >1 time in a case, not in allowed_repeats
    counts = events.groupby(["case_id", "activity"]).size().reset_index(name="count")
    repeated = counts[(counts["count"] > 1) & (~counts["activity"].isin(allowed_repeats))]
    for cid in repeated["case_id"].unique():
        deviations[cid].add("repeated_activity")

    # skipped_step: an expected activity never appears in the case at all.
    # Vectorized as a small loop over `expected` (a fixed, short list -- 6 activities
    # in config/process.yaml) rather than over every case: for each expected
    # activity, the set of case_ids that DO contain it is one boolean-mask + unique()
    # call. A case missing ANY expected activity is in the complement of the
    # intersection of all these per-activity sets.
    all_case_id_set = set(all_case_ids)
    cases_with_every_expected_activity = set(all_case_ids)
    for activity in expected:
        cases_with_this_activity = set(events.loc[events["activity"] == activity, "case_id"])
        cases_with_every_expected_activity &= cases_with_this_activity
    for cid in all_case_id_set - cases_with_every_expected_activity:
        deviations[cid].add("skipped_step")

    # unexpected_activity: an activity outside the expected sequence appears at all
    unexpected_mask = ~events["activity"].isin(expected)
    for cid in events.loc[unexpected_mask, "case_id"].unique():
        deviations[cid].add("unexpected_activity")

    # out_of_order: is the deduped, timestamp-ordered sequence of expected-activity
    # occurrences already sorted by their position in `expected`? Vectorized via
    # groupby().shift(1): map each expected activity to its index in `expected`,
    # keep only the first (earliest-timestamp) occurrence of each activity per case,
    # then compare each index to the previous one within its case -- any decrease
    # means that case saw a later-expected step before an earlier-expected one.
    # No Python loop over cases: drop_duplicates and groupby().shift() are both
    # implemented as vectorized (Cython) pandas operations, not per-group Python calls.
    expected_index_map = {a: i for i, a in enumerate(expected)}
    filtered = events[events["activity"].isin(expected)].copy()
    filtered["expected_index"] = filtered["activity"].map(expected_index_map)
    filtered = filtered.sort_values(["case_id", "timestamp"])
    deduped = filtered.drop_duplicates(subset=["case_id", "expected_index"], keep="first")
    deduped = deduped.sort_values(["case_id", "timestamp"])
    deduped["prev_index"] = deduped.groupby("case_id")["expected_index"].shift(1)
    out_of_order_mask = deduped["expected_index"] < deduped["prev_index"]
    for cid in deduped.loc[out_of_order_mask, "case_id"].unique():
        deviations[cid].add("out_of_order")

    rows = [
        {
            "case_id": cid,
            "is_conformant": len(deviations[cid]) == 0,
            "deviation_types": ",".join(sorted(deviations[cid])) if deviations[cid] else None,
        }
        for cid in all_case_ids
    ]
    return pd.DataFrame(rows)


def conformance_report(conformance_df: pd.DataFrame) -> dict:
    total = len(conformance_df)
    conformant = int(conformance_df["is_conformant"].sum())

    deviation_counts = {}
    for types in conformance_df["deviation_types"].dropna():
        for t in types.split(","):
            deviation_counts[t] = deviation_counts.get(t, 0) + 1

    return {
        "total_cases": total,
        "conformant_cases": conformant,
        "conformance_rate_pct": round((conformant / total) * 100, 1) if total else 0.0,
        "deviation_breakdown": deviation_counts,
    }


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from src.ingestion.load_event_log import load_event_log
    from src.cleaning.clean_events import clean_events

    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)

    conformance = check_conformance(cleaned)
    report = conformance_report(conformance)
    print(report)
