"""
Phase 30: real, runnable performance measurement -- times each pipeline stage against
an actual dataset, not a projection. Uses the golden dataset by default (small, but
genuinely exercises every stage); pass --synthetic to generate a larger set first and
benchmark against that instead, for a more realistic sense of how timing scales.

This is deliberately scoped to what's measurable without live infrastructure: stage
duration and row counts. Query cost (BigQuery) and $ estimates need a real BigQuery
project to measure for real -- see docs/performance-notes.md for what's stated as an
estimate vs. what's actually measured here.
"""
import argparse
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.ingestion.load_event_log import load_event_log
from src.ingestion.data_contract import enforce_data_contract
from src.ingestion.validate_input import validate_event_log
from src.cleaning.clean_events import clean_events
from src.transformation.build_process_cases import build_process_cases
from src.analytics.cycle_time import cycle_time_percentiles, stage_summary
from src.analytics.bottlenecks import identify_bottlenecks
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla
from src.analytics.conformance import check_conformance
from src.analytics.rework import rework_by_case
from src.analytics.supplier_analysis import supplier_scorecard


@dataclass
class StageTiming:
    stage: str
    duration_ms: float
    row_count: int | None = None


def _generate_synthetic_csv(path: str, n_cases: int = 2000) -> None:
    """Generates a larger synthetic event log (raw XES-style columns) purely for
    benchmarking at a more realistic scale than the 7-case golden set -- NOT used for
    correctness auditing (that's Phase 27's job, and it needs hand-verifiable values,
    which a randomly generated set of this size can't provide)."""
    rng = np.random.default_rng(42)
    activities = ["Purchase Requisition", "Approval", "Purchase Order", "Goods Receipt", "Invoice Receipt", "Payment"]
    rows = []
    for i in range(n_cases):
        case_id = f"BENCH{i:05d}"
        start = pd.Timestamp("2024-01-01") + pd.Timedelta(hours=int(rng.integers(0, 24 * 300)))
        t = start
        for activity in activities:
            rows.append({
                "case:concept:name": case_id, "concept:name": activity, "time:timestamp": t,
                "case:Spend area text": rng.choice(["3-way match", "Consignment"]),
                "case:Vendor": rng.choice(["S1", "S2", "S3", "S4", "S5"]),
            })
            t = t + pd.Timedelta(hours=float(rng.exponential(20)))
    pd.DataFrame(rows).to_csv(path, index=False)


def run_benchmark(source_path: str) -> list[StageTiming]:
    timings = []

    def timed(label, fn, *args, row_count_fn=None):
        t0 = time.monotonic()
        result = fn(*args)
        duration_ms = (time.monotonic() - t0) * 1000
        row_count = row_count_fn(result) if row_count_fn else None
        timings.append(StageTiming(stage=label, duration_ms=round(duration_ms, 2), row_count=row_count))
        return result

    raw = timed("load_event_log", load_event_log, source_path, row_count_fn=len)
    timed("enforce_data_contract", enforce_data_contract, raw)
    timed("validate_event_log", validate_event_log, raw)
    cleaned, _ = timed("clean_events", clean_events, raw, row_count_fn=lambda r: len(r[0]))
    cases = timed("build_process_cases", build_process_cases, cleaned, row_count_fn=len)
    timed("cycle_time_percentiles", cycle_time_percentiles, cases)
    timed("stage_summary", stage_summary, cleaned)
    timed("identify_bottlenecks", identify_bottlenecks, cleaned)
    evaluated = timed("evaluate_sla", evaluate_sla, cases, load_sla_targets(), row_count_fn=len)
    timed("rework_by_case", rework_by_case, cleaned)
    try:
        timed("check_conformance", check_conformance, cleaned)
    except FileNotFoundError:
        pass  # config/process.yaml missing in whatever CWD this runs from -- skip rather than crash the benchmark
    try:
        timed("supplier_scorecard", supplier_scorecard, evaluated, 2)
    except ValueError:
        pass  # no supplier_id column in this source -- skip

    return timings


def print_report(timings: list[StageTiming]) -> None:
    total = sum(t.duration_ms for t in timings)
    print(f"{'Stage':<25} {'Duration (ms)':>15} {'Rows':>10}")
    print("-" * 52)
    for t in timings:
        rows = str(t.row_count) if t.row_count is not None else "-"
        print(f"{t.stage:<25} {t.duration_ms:>15.2f} {rows:>10}")
    print("-" * 52)
    print(f"{'TOTAL':<25} {total:>15.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic", action="store_true", help="Generate and benchmark against a larger synthetic set instead of the golden dataset")
    parser.add_argument("--n-cases", type=int, default=2000)
    args = parser.parse_args()

    if args.synthetic:
        path = "/tmp/benchmark_synthetic_events.csv"
        print(f"Generating {args.n_cases} synthetic cases...")
        _generate_synthetic_csv(path, args.n_cases)
    else:
        path = "data/golden/golden_raw_events.csv"

    timings = run_benchmark(path)
    print_report(timings)
