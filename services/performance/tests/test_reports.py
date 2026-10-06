import pandas as pd
from src.reports.generate_summary import build_executive_summary, filter_period, filter_segment
from src.reports.generate_management_report import build_management_report, render_markdown


def sample_events():
    return pd.DataFrame({
        "case_id": ["C1", "C1", "C2", "C2", "C3", "C3"],
        "activity": ["Created", "Approved", "Created", "Approved", "Created", "Approved"],
        "timestamp": pd.to_datetime([
            "2024-01-01 09:00", "2024-01-03 09:00",
            "2024-01-05 09:00", "2024-01-06 09:00",
            "2024-02-01 09:00", "2024-02-15 09:00",
        ]),
        "category": ["3-way match", "3-way match", "3-way match", "3-way match", "Consignment", "Consignment"],
        "supplier_id": ["S1", "S1", "S1", "S1", "S2", "S2"],
    })


def sample_cases():
    return pd.DataFrame({
        "case_id": ["C1", "C2", "C3"],
        "cycle_time_hours": [48.0, 24.0, 336.0],
        "category": ["3-way match", "3-way match", "Consignment"],
        "supplier_id": ["S1", "S1", "S2"],
        "event_count": [2, 2, 2],
        "variant_frequency": [2, 2, 1],
        "start_time": pd.to_datetime(["2024-01-01", "2024-01-05", "2024-02-01"]),
        "end_time": pd.to_datetime(["2024-01-03", "2024-01-06", "2024-02-15"]),
    })


def test_filter_period_scopes_by_date_range():
    cases = sample_cases()
    filtered = filter_period(cases, start="2024-01-01", end="2024-01-31")
    assert set(filtered["case_id"]) == {"C1", "C2"}


def test_filter_segment_scopes_by_category():
    cases = sample_cases()
    filtered = filter_segment(cases, "category", "Consignment")
    assert set(filtered["case_id"]) == {"C3"}


def test_build_executive_summary_counts_cases_correctly():
    summary = build_executive_summary(sample_events(), sample_cases())
    assert summary.total_cases == 3
    assert summary.avg_cycle_time_hours > 0


def test_build_executive_summary_empty_period_returns_zeroed_summary():
    summary = build_executive_summary(
        sample_events(), sample_cases(), start="2030-01-01"
    )
    assert summary.total_cases == 0
    assert summary.avg_cycle_time_hours == 0.0


def test_management_report_has_at_least_one_finding():
    report = build_management_report(sample_events(), sample_cases())
    assert len(report["findings"]) >= 1
    assert all(f.priority in {"HIGH", "MEDIUM", "LOW"} for f in report["findings"])


def test_render_markdown_includes_all_findings():
    report = build_management_report(sample_events(), sample_cases())
    md = render_markdown(report)
    assert "# Operations Performance" in md
    for f in report["findings"]:
        assert f.title in md
