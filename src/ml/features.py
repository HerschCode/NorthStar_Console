import os

import numpy as np
import pandas as pd

# SQL source mapping (both SQL and Python consume the same case-level data):
#   event_count         -- sourced from build_process_cases() aggregation; parallel
#                          SQL view: sql/analysis/cycle_time.sql (event counts per case)
#   variant_frequency   -- sourced from build_process_cases(); parallel SQL view:
#                          sql/analysis/process_variants.sql (variant distribution)
#   supplier_id         -- sourced from raw event log; parallel SQL analytics:
#                          sql/analysis/supplier_performance.sql
#   category            -- sourced from raw event log; SLA thresholds from
#                          sql/analysis/sla_performance.sql
#   first_activity      -- sourced from build_process_cases() (first event per case)
#   supplier_historical_breach_rate -- no SQL equivalent; computed in Python only
#                          from supplier cases completed before each case starts

# Feature families for ablation study (scripts/ablation_study.py):
#   baseline         — structural case-level signals available immediately
#   temporal         — time-of-day / calendar signals from start_time only
#   process          — process-mining signals from the event sequence
#   supplier_history — only supplier cases completed before the current case
# All families together = FEATURE_COLUMNS below.

FEATURE_COLUMNS = [
    # ── baseline ────────────────────────────────────────────────────────
    "event_count",
    "variant_frequency",
    "category",
    "supplier_id",
    # ── temporal ────────────────────────────────────────────────────────
    "start_hour",
    "start_dayofweek",
    "start_month",         # captures intra-year seasonality (year-end rush, slow August)
    "start_quarter",       # coarser seasonal bucket useful when month is too sparse
    # ── process ─────────────────────────────────────────────────────────
    "first_activity",
    "last_activity",
    "unique_activity_count",   # breadth of the process path
    "rework_count",            # repeated activities = re-work or system glitch; correlates
                               # with delay and non-conformance (see conformance analysis)
    # ── supplier history ────────────────────────────────────────────────
    "supplier_historical_breach_rate",      # completed-case-only supplier history
    "supplier_historical_median_cycle_time",# completed-case-only duration history
    "sla_target_hours",    # SLA threshold for this case's category; set by config before
                           # the case ends, so this is not leakage — tighter targets make
                           # breach more likely and give the model a direct numeric signal
]

FEATURE_FAMILIES = {
    "baseline": ["event_count", "variant_frequency", "category", "supplier_id"],
    "temporal": ["start_hour", "start_dayofweek", "start_month", "start_quarter"],
    "process":  ["first_activity", "last_activity", "unique_activity_count", "rework_count"],
    "supplier_history": [
        "supplier_historical_breach_rate",
        "supplier_historical_median_cycle_time",
        "sla_target_hours",
    ],
}


def _add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Adds features beyond the raw case-level columns. Each one is checked for
    leakage before being added -- a feature computed from information only
    available once the case is already finished (e.g. anything derived from
    end_time or cycle_time_hours, which is literally how sla_breach itself is
    defined) would let the model see its own target, so none of these touch
    those columns."""
    if "start_time" in df.columns:
        df["start_hour"] = df["start_time"].dt.hour
        df["start_dayofweek"] = df["start_time"].dt.dayofweek
        df["start_month"] = df["start_time"].dt.month
        df["start_quarter"] = df["start_time"].dt.quarter

    # A prior case is usable only after it has ended. Earlier start order alone
    # is insufficient because cases overlap in time. Fixed sentinels avoid using
    # full-dataset target/duration statistics for cases with no completed history.
    if (
        {"supplier_id", "start_time", "end_time", "sla_breach", "cycle_time_hours"}
        <= set(df.columns)
    ):
        df["supplier_historical_breach_rate"] = 0.5
        df["supplier_historical_median_cycle_time"] = -1.0

        for _, supplier_cases in df.dropna(subset=["supplier_id"]).groupby("supplier_id", sort=False):
            query_starts = supplier_cases["start_time"].to_numpy()

            completed_breaches = supplier_cases.dropna(subset=["end_time", "sla_breach"]).sort_values("end_time")
            if not completed_breaches.empty:
                completion_times = completed_breaches["end_time"].to_numpy()
                completed_counts = np.searchsorted(completion_times, query_starts, side="left")
                breach_values = completed_breaches["sla_breach"].astype(int).to_numpy()
                breach_rates = np.cumsum(breach_values) / np.arange(1, len(breach_values) + 1)
                has_history = completed_counts > 0
                df.loc[supplier_cases.index[has_history], "supplier_historical_breach_rate"] = breach_rates[
                    completed_counts[has_history] - 1
                ]

            completed_durations = supplier_cases.dropna(
                subset=["end_time", "cycle_time_hours"]
            ).sort_values("end_time")
            if not completed_durations.empty:
                completion_times = completed_durations["end_time"].to_numpy()
                completed_counts = np.searchsorted(completion_times, query_starts, side="left")
                prefix_medians = completed_durations["cycle_time_hours"].expanding().median().to_numpy()
                has_history = completed_counts > 0
                df.loc[supplier_cases.index[has_history], "supplier_historical_median_cycle_time"] = prefix_medians[
                    completed_counts[has_history] - 1
                ]

    return df


def raw_feature_frame(evaluated_cases: pd.DataFrame) -> pd.DataFrame:
    """The model's inputs BEFORE one-hot encoding (one column per FEATURE_COLUMNS entry), same
    derivation as build_features() -- what src/ml/feature_drift.py measures drift on, so a
    supplier-mix shift shows up as ONE `supplier_id` drift number, not 486 dummy columns."""
    df = _add_derived_features(evaluated_cases.copy())
    return df[[c for c in FEATURE_COLUMNS if c in df.columns]].copy()


def build_features(evaluated_cases: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    df = _add_derived_features(evaluated_cases.copy())

    available = [c for c in FEATURE_COLUMNS if c in df.columns]
    if not available:
        raise ValueError("None of the expected feature columns are present")

    X = df[available].copy()

    for col in ["category", "supplier_id", "first_activity", "last_activity"]:
        if col in X.columns:
            X[col] = X[col].fillna("UNKNOWN")
            X = pd.get_dummies(X, columns=[col], prefix=col)

    y = df["sla_breach"].astype(int)
    return X, y


# PLAN.md Phase 2: "Have the ML feature builder optionally read from fct_cases (flag), results
# unchanged." fct_cases (dbt/models/marts/fct_cases.sql) already carries every base column
# evaluate_sla() + build_process_cases() would otherwise compute from raw events --
# case_id/category/supplier_id/start_time/end_time/cycle_time_hours/sla_target_hours/
# sla_breach/event_count/variant/variant_frequency/first_activity/last_activity -- so this
# swaps only the SOURCE query, not _add_derived_features()/build_features() themselves, which
# is what "results unchanged" actually requires: the same downstream code runs either way.
# Not the default -- opt-in via FEATURE_SOURCE=dbt_marts, since running `dbt build` is an extra
# step this project's existing pipeline (scripts/run_pipeline.py) doesn't require.
USE_DBT_MARTS_ENV_VAR = "FEATURE_SOURCE"


def feature_source_is_dbt_marts() -> bool:
    return os.environ.get(USE_DBT_MARTS_ENV_VAR, "").strip().lower() == "dbt_marts"


def load_evaluated_cases_from_dbt_marts(engine) -> pd.DataFrame:
    """Reads dbt_marts.fct_cases instead of the raw-events path, with column names matching
    what evaluate_sla() would have produced, so the caller can hand the result straight to
    build_features() unmodified. `has_rework`/`rework_event_count` are dbt-only extras (harmless
    -- build_features() only pulls columns it recognizes from FEATURE_COLUMNS)."""
    return pd.read_sql(
        """
        select case_id, purchase_order_id, category, supplier_id, start_time, end_time, cycle_time_hours,
               sla_target_hours, sla_breach, event_count, variant, variant_frequency,
               first_activity, last_activity
        from dbt_marts.fct_cases
        where cycle_time_hours > 0   -- same exclusion as evaluate_sla(): zero-duration cases are truncated records
        """,
        engine,
    )


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    from src.ingestion.load_event_log import load_event_log
    from src.cleaning.clean_events import clean_events
    from src.transformation.build_process_cases import build_process_cases
    from src.analytics.sla_analysis import load_sla_targets, evaluate_sla

    load_dotenv()
    raw = load_event_log(os.environ["RAW_EVENT_LOG_PATH"])
    cleaned, _ = clean_events(raw)
    cases = build_process_cases(cleaned)
    evaluated = evaluate_sla(cases, load_sla_targets())

    X, y = build_features(evaluated)
    print(f"Feature matrix: {X.shape}, breach rate: {y.mean():.2%}")
    print(X.columns.tolist())
