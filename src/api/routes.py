from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import PlainTextResponse
import pandas as pd

from src.api.db import load_events, load_cases, load_case_events, check_connection, get_engine, get_write_engine
from src.api.auth import require_api_key, require_role
from src.api.metrics import PREDICTIONS_TOTAL, render_metrics
from src.api.schemas import (
    HealthResponse,
    CycleTimeResponse,
    CycleTimeSegmentRow,
    BottleneckRow,
    SlaSummaryRow,
    SupplierScoreRow,
    SlaRiskResponse,
    FactorExplanation,
    PipelineRunRow,
    ConformanceResponse,
    SlaRiskDistributionResponse,
    SlaRiskBucket,
    DataQualityCheck,
    DataQualityReport,
    RiskBucketDrift,
    PredictionDriftReport,
    FeatureDriftRow,
    FeatureDriftReportSchema,
    InterventionCreate,
    InterventionCreated,
    EarlyRiskResponse,
)
from src.analytics.cycle_time import cycle_time_percentiles, stage_summary
from src.analytics.bottlenecks import identify_bottlenecks
from src.analytics.sla_analysis import load_sla_targets, evaluate_sla, sla_summary
from src.analytics.supplier_analysis import supplier_scorecard
from src.analytics.conformance import check_conformance, conformance_report
from src.ml.predict import load_model, predict_sla_risk
from src.ml.features import build_features
from src.ml.explain_shap import explain_prediction_shap
from src.reports.generate_management_report import build_management_report, render_markdown

router = APIRouter()

# /health is intentionally on an unauthenticated router -- see src/api/auth.py's
# docstring for why (load balancer / orchestrator liveness probes conventionally
# don't carry a credential). Every other route lives on `router`, which
# src/api/main.py includes WITH the require_api_key dependency applied.
health_router = APIRouter()


@health_router.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", database_connected=check_connection())


@health_router.get("/metrics", include_in_schema=False)
def metrics():
    """Prometheus scrape target. Unauthenticated, same convention as /health --
    a monitoring system polling this every 15-30s shouldn't need a credential
    any more than a load balancer's health check does. See src/api/metrics.py
    and src/api/middleware.py for what's actually instrumented (request count
    and latency histogram by route+status, plus a prediction counter by
    risk_level)."""
    body, content_type = render_metrics()
    return PlainTextResponse(content=body.decode("utf-8"), media_type=content_type)


@health_router.get("/health/model")
def model_health():
    """Unauthenticated model health endpoint — returns model version, staleness,
    prediction distribution, and a retrain recommendation flag. Used by the
    dashboard widget and external monitoring without requiring an API key."""
    import json
    from pathlib import Path
    from datetime import datetime, timezone

    meta_path = Path("models/sla_risk_model.meta.json")
    if not meta_path.exists():
        raise HTTPException(
            status_code=503,
            detail="No trained model found — run src/ml/train.py first",
        )

    meta = json.loads(meta_path.read_text())
    trained_at = datetime.fromisoformat(meta["trained_at"])
    now = datetime.now(timezone.utc)
    staleness_days = (now - trained_at).days

    dist = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    try:
        engine = get_engine()
        df = pd.read_sql(
            "SELECT risk_level, COUNT(*) AS n FROM analytics.sla_predictions GROUP BY risk_level",
            engine,
        )
        for _, row in df.iterrows():
            lvl = str(row["risk_level"]).upper()
            if lvl in dist:
                dist[lvl] = int(row["n"])
    except Exception:
        pass

    return {
        "status": "ok",
        "model_name": meta.get("model_name"),
        "roc_auc": meta.get("roc_auc"),
        "trained_at": meta.get("trained_at"),
        "staleness_days": staleness_days,
        "train_row_count": meta.get("train_row_count"),
        "prediction_distribution": dist,
        "retrain_recommended": staleness_days > 30,
    }


@router.get("/metrics/cycle-time")
def cycle_time(segment: str | None = Query(default=None, description="Segment by column, e.g. 'category'")):
    cases = load_cases()
    if cases.empty:
        raise HTTPException(status_code=404, detail="No process cases loaded yet -- run the pipeline first")
    if segment:
        if segment not in cases.columns:
            raise HTTPException(status_code=400, detail=f"Unknown segment '{segment}'. Valid: {list(cases.columns)}")
        grp = cases.groupby(segment)["cycle_time_hours"]
        rows = [
            CycleTimeSegmentRow(
                segment=str(cat) if cat is not None else None,
                mean_hours=round(float(s.mean()), 2),
                median_hours=round(float(s.median()), 2),
                p90_hours=round(float(s.quantile(0.9)), 2),
                case_count=int(len(s)),
            )
            for cat, s in grp
        ]
        return sorted(rows, key=lambda r: r.mean_hours, reverse=True)
    return cycle_time_percentiles(cases)


@router.get("/metrics/bottlenecks", response_model=list[BottleneckRow])
def bottlenecks(top_n: int = Query(default=10, ge=1, le=50)):
    events = load_events()
    if events.empty:
        raise HTTPException(status_code=404, detail="No events loaded yet -- run the pipeline first")
    result = identify_bottlenecks(events, top_n=top_n)
    return result.to_dict(orient="records")


@router.get("/metrics/sla", response_model=list[SlaSummaryRow])
def sla_metrics(segment: str | None = Query(default=None, description="e.g. 'category'")):
    cases = load_cases()
    if cases.empty:
        raise HTTPException(status_code=404, detail="No process cases loaded yet -- run the pipeline first")

    evaluated = evaluate_sla(cases, load_sla_targets())
    summary = sla_summary(evaluated, segment_col=segment)

    segment_col = segment if segment and segment in summary.columns else "_all"
    rows = []
    for _, row in summary.iterrows():
        rows.append(SlaSummaryRow(
            segment=str(row[segment_col]) if segment_col in row else None,
            case_count=int(row["case_count"]),
            breach_count=int(row["breach_count"]),
            breach_rate_pct=float(row["breach_rate_pct"]),
        ))
    return rows


@router.get("/suppliers/performance", response_model=list[SupplierScoreRow])
def supplier_performance(min_volume: int = Query(default=5, ge=1)):
    cases = load_cases()
    if "supplier_id" not in cases.columns:
        raise HTTPException(status_code=404, detail="This dataset export has no supplier_id column")

    evaluated = evaluate_sla(cases, load_sla_targets())
    scorecard = supplier_scorecard(evaluated, min_volume=min_volume)
    return scorecard.to_dict(orient="records")


@router.get("/orders/{case_id}/risk", response_model=SlaRiskResponse)
def order_risk(case_id: str, explain: bool = Query(default=False, description="Include SHAP top-5 feature contributions in the response")):
    cases = load_cases()
    match = cases[cases["case_id"] == case_id]
    if match.empty:
        raise HTTPException(status_code=404, detail=f"No case found with case_id '{case_id}'")

    sla_targets = load_sla_targets()
    evaluated = evaluate_sla(match, sla_targets, keep_unmeasurable=True)  # score this case even if truncated
    X, _ = build_features(evaluated)

    try:
        bundle = load_model()
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="No trained SLA-risk model available -- run src/ml/train.py first",
        )

    prediction = predict_sla_risk(X, bundle)

    explanation = None
    if explain:
        # Use all cases as SHAP background (already loaded); align to the same
        # column set the model was trained on so the explainer sees the right shape.
        all_evaluated = evaluate_sla(cases, sla_targets)
        X_all, _ = build_features(all_evaluated)
        cols = bundle["columns"]
        X_all_aligned = X_all.reindex(columns=cols, fill_value=0)
        X_case_aligned = X.reindex(columns=cols, fill_value=0)
        raw = explain_prediction_shap(bundle["model"], X_all_aligned, X_case_aligned, top_n=5)
        explanation = [FactorExplanation(**f) for f in raw]

    risk_level = str(prediction.iloc[0]["risk_level"])
    PREDICTIONS_TOTAL.labels(risk_level=risk_level).inc()

    return SlaRiskResponse(
        case_id=case_id,
        breach_probability=float(prediction.iloc[0]["breach_probability"]),
        risk_level=risk_level,
        explanation=explanation,
    )


@router.get("/reports/management", response_class=PlainTextResponse)
def management_report():
    """Returns the current Finding -> Evidence -> Impact -> Recommendation report as
    markdown. This is what a scheduled job (or operations-assistant) would call to get
    the same report a human would generate by running generate_management_report.py
    directly -- one code path, two consumers."""
    events = load_events()
    cases = load_cases()
    if events.empty or cases.empty:
        raise HTTPException(status_code=404, detail="No data loaded yet -- run the pipeline first")

    report = build_management_report(events, cases)
    return render_markdown(report)


@router.get("/observability/pipeline-runs", response_model=list[PipelineRunRow])
def pipeline_runs(limit: int = Query(default=20, ge=1, le=200)):
    """Recent pipeline run history from the Phase 5 audit table -- surfaces run
    duration/success/failure without needing direct DB access. This is the
    observability layer's window into 'is the pipeline healthy', same idea as
    the request-logging middleware is for the API itself."""
    engine = get_engine()
    query = """
        SELECT run_id, started_at, finished_at, status,
               raw_row_count, cleaned_row_count, case_count, error_message
        FROM analytics.pipeline_runs
        ORDER BY started_at DESC
        LIMIT %(limit)s
    """
    df = pd.read_sql(query, engine, params={"limit": limit})
    df["started_at"] = df["started_at"].astype(str)
    df["finished_at"] = df["finished_at"].astype(str)
    df["finished_at"] = df["finished_at"].where(df["finished_at"] != "NaT", other=None)
    # Failed runs leave count columns as float NaN. to_dict() preserves NaN as Python
    # float nan, which Pydantic v2 rejects for int | None -- it only accepts finite
    # numbers or actual None. Cast to object first so None stays None through to_dict.
    for col in ("raw_row_count", "cleaned_row_count", "case_count"):
        df[col] = df[col].astype(object).where(pd.notna(df[col]), other=None)
    return df.to_dict(orient="records")


@health_router.get("/observability/data-quality", response_model=DataQualityReport)
def data_quality():
    """Live data quality report over the analytics schema. Runs a set of named
    checks — row counts, null rates, timestamp freshness, prediction coverage —
    and returns per-check pass/warn/fail results plus an overall rollup status.
    Useful for pipeline health monitoring without needing direct DB access."""
    from datetime import datetime, timezone, timedelta

    engine = get_engine()
    checks: list[DataQualityCheck] = []

    def _check(name: str, query: str, *, warn_fn=None, fail_fn=None, fmt=str) -> DataQualityCheck:
        try:
            result = pd.read_sql(query, engine).iloc[0, 0]
            value = fmt(result)
            if fail_fn and fail_fn(result):
                status = "fail"
            elif warn_fn and warn_fn(result):
                status = "warn"
            else:
                status = "pass"
            return DataQualityCheck(name=name, status=status, value=value)
        except Exception as exc:
            return DataQualityCheck(name=name, status="fail", value="error", detail=str(exc)[:120])

    # ── Events ──
    checks.append(_check(
        "events.row_count",
        "SELECT COUNT(*) FROM staging.events",
        warn_fn=lambda n: n < 100,
        fail_fn=lambda n: n == 0,
        fmt=lambda n: f"{int(n):,} rows",
    ))
    checks.append(_check(
        "events.null_case_id_rate",
        "SELECT ROUND(100.0 * SUM(CASE WHEN case_id IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2) FROM staging.events",
        warn_fn=lambda r: r > 0.5,
        fail_fn=lambda r: r > 5.0,
        fmt=lambda r: f"{float(r):.2f}%",
    ))
    checks.append(_check(
        "events.null_activity_rate",
        "SELECT ROUND(100.0 * SUM(CASE WHEN activity IS NULL THEN 1 ELSE 0 END) / COUNT(*), 2) FROM staging.events",
        warn_fn=lambda r: r > 0.1,
        fail_fn=lambda r: r > 1.0,
        fmt=lambda r: f"{float(r):.2f}%",
    ))

    # ── Timestamp freshness ──
    try:
        ts_df = pd.read_sql(
            "SELECT MAX(timestamp) AS latest FROM staging.events", engine
        )
        latest = pd.to_datetime(ts_df.iloc[0, 0])
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - latest).days
        if age_days > 365:
            status = "warn"
        else:
            status = "pass"
        checks.append(DataQualityCheck(
            name="events.latest_event_age_days",
            status=status,
            value=f"{age_days} days",
            detail="BPI 2019 corpus is static; age reflects corpus date, not pipeline failure" if age_days > 30 else None,
        ))
    except Exception as exc:
        checks.append(DataQualityCheck(name="events.latest_event_age_days", status="fail", value="error", detail=str(exc)[:120]))

    # ── Process cases ──
    checks.append(_check(
        "cases.row_count",
        "SELECT COUNT(*) FROM analytics.process_cases",
        warn_fn=lambda n: n < 10,
        fail_fn=lambda n: n == 0,
        fmt=lambda n: f"{int(n):,} cases",
    ))
    checks.append(_check(
        "cases.avg_events_per_case",
        "SELECT ROUND(AVG(c.n), 1) FROM (SELECT case_id, COUNT(*) AS n FROM staging.events GROUP BY case_id) c",
        warn_fn=lambda v: v < 2,
        fail_fn=lambda v: v < 1,
        fmt=lambda v: f"{float(v):.1f}",
    ))

    # ── SLA predictions ──
    checks.append(_check(
        "predictions.row_count",
        "SELECT COUNT(*) FROM analytics.sla_predictions",
        warn_fn=lambda n: n < 10,
        fail_fn=lambda n: n == 0,
        fmt=lambda n: f"{int(n):,} predictions",
    ))
    checks.append(_check(
        "predictions.high_risk_rate",
        "SELECT ROUND(100.0 * SUM(CASE WHEN risk_level = 'HIGH' THEN 1 ELSE 0 END) / COUNT(*), 1) FROM analytics.sla_predictions",
        # Real crash, found live: 0 rows in analytics.sla_predictions makes the SQL's own
        # division 0/0 = NULL, and float(None) raised an unhandled TypeError -- every warn_fn/
        # fail_fn/fmt lambda here assumed a real number and crashed instead of reporting "no
        # predictions yet" cleanly. Guarded explicitly rather than relying on _check's broad
        # except to paper over it with a generic "error" status.
        warn_fn=lambda r: r is not None and r > 80,
        fail_fn=lambda r: r is not None and r > 95,
        fmt=lambda r: f"{float(r):.1f}%" if r is not None else "n/a (no predictions yet)",
    ))

    overall = "pass"
    if any(c.status == "fail" for c in checks):
        overall = "fail"
    elif any(c.status == "warn" for c in checks):
        overall = "warn"

    return DataQualityReport(
        overall=overall,
        checked_at=datetime.now(timezone.utc).isoformat(),
        checks=checks,
    )


@health_router.get("/health/drift/features", response_model=FeatureDriftReportSchema)
def feature_drift():
    """Per-feature Population Stability Index of the most recent `current_window_cases` cases vs
    the deployed model's training-time baselines (models/sla_risk_model.meta.json's
    feature_baselines). Complements /observability/prediction-drift, which only sees the model's
    OUTPUT distribution -- inputs shift first. Thresholds: config/drift.yaml."""
    from datetime import datetime, timezone
    from src.ml.feature_drift import current_feature_drift, load_drift_config

    cfg = load_drift_config()
    try:
        report = current_feature_drift()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"feature drift unavailable: {str(exc)[:120]}")
    return FeatureDriftReportSchema(
        status=report.status,
        checked_at=datetime.now(timezone.utc).isoformat(),
        n_current_rows=report.n_current_rows,
        alert_features=report.alert_features,
        features=[FeatureDriftRow(feature=f.feature, psi=f.psi, status=f.status) for f in report.features],
        thresholds={"psi_warn": cfg["psi_warn"], "psi_alert": cfg["psi_alert"],
                    "retrain_min_alert_features": cfg["retrain_min_alert_features"]},
        note=report.note,
    )


@health_router.get("/observability/prediction-drift", response_model=PredictionDriftReport)
def prediction_drift():
    """Compare the current prediction risk-level distribution against the
    training-time baseline stored in sla_risk_model.meta.json. A >10pp shift
    in any bucket is a warn; >20pp is an alert — thresholds that flag genuine
    distribution change without triggering on normal batch-to-batch variance.
    Returns 'no_baseline' if the model was trained before this field was added."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    checked_at = datetime.now(timezone.utc).isoformat()
    meta_path = Path("models/sla_risk_model.meta.json")
    if not meta_path.exists():
        raise HTTPException(status_code=503, detail="No trained model found")

    meta = json.loads(meta_path.read_text())
    baseline = meta.get("train_risk_distribution")
    if not baseline or not any(baseline.values()):
        return PredictionDriftReport(
            status="no_baseline",
            checked_at=checked_at,
            total_current_predictions=0,
            baseline_source="none — retrain with current code to populate",
            buckets=[],
            note="train_risk_distribution not present in meta; run src/ml/train.py to regenerate",
        )

    baseline_total = sum(baseline.values())
    baseline_pcts = {k: round(100 * v / baseline_total, 1) for k, v in baseline.items()}

    engine = get_engine()
    current_dist = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    try:
        df = pd.read_sql(
            "SELECT risk_level, COUNT(*) AS n FROM analytics.sla_predictions GROUP BY risk_level",
            engine,
        )
        for _, row in df.iterrows():
            lvl = str(row["risk_level"]).upper()
            if lvl in current_dist:
                current_dist[lvl] = int(row["n"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not read predictions: {exc}")

    current_total = sum(current_dist.values())
    if current_total == 0:
        raise HTTPException(status_code=404, detail="No predictions found — run the pipeline first")

    current_pcts = {k: round(100 * v / current_total, 1) for k, v in current_dist.items()}

    buckets = []
    worst = "stable"
    for bucket in ["LOW", "MEDIUM", "HIGH"]:
        delta = round(current_pcts[bucket] - baseline_pcts.get(bucket, 0.0), 1)
        abs_delta = abs(delta)
        if abs_delta >= 20:
            status = "alert"
            worst = "alert"
        elif abs_delta >= 10:
            status = "warn"
            if worst != "alert":
                worst = "warn"
        else:
            status = "stable"
        buckets.append(RiskBucketDrift(
            bucket=bucket,
            baseline_pct=baseline_pcts.get(bucket, 0.0),
            current_pct=current_pcts[bucket],
            delta_pct=delta,
            status=status,
        ))

    return PredictionDriftReport(
        status=worst,
        checked_at=checked_at,
        total_current_predictions=current_total,
        baseline_source=f"test-set predictions from training run at {meta.get('trained_at', 'unknown')}",
        buckets=buckets,
    )


@router.get("/metrics/conformance", response_model=ConformanceResponse)
def conformance():
    """Wraps src/analytics/conformance.py's check against config/process.yaml's
    expected sequence. Unlocks dashboard page 5 (dashboard/README.md) and
    operations-assistant's src/tools/process.py, both of which were documented as
    waiting on this endpoint existing."""
    events = load_events()
    if events.empty:
        raise HTTPException(status_code=404, detail="No events loaded yet -- run the pipeline first")

    try:
        result = check_conformance(events)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="config/process.yaml not found -- conformance checking is not configured")

    report = conformance_report(result)
    return ConformanceResponse(
        total_cases=report["total_cases"],
        conformant_cases=report["conformant_cases"],
        conformance_rate_pct=report["conformance_rate_pct"],
        deviation_breakdown=report["deviation_breakdown"],
    )


@router.get("/metrics/sla-risk-distribution", response_model=SlaRiskDistributionResponse)
def sla_risk_distribution():
    """Batch SLA-risk prediction across every case, bucketed by risk level -- unlocks
    dashboard page 6, which per-case /orders/{id}/risk can't reasonably serve (that
    endpoint is documented as single-case-at-a-time; looping it per case for a
    dashboard view was explicitly flagged as the wrong approach when the dashboard
    spec was written). predict_sla_risk already accepts a full DataFrame natively --
    this endpoint doesn't loop, it scores every case in one batch call."""
    cases = load_cases()
    if cases.empty:
        raise HTTPException(status_code=404, detail="No process cases loaded yet -- run the pipeline first")

    evaluated = evaluate_sla(cases, load_sla_targets())
    X, _ = build_features(evaluated)

    try:
        bundle = load_model()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="No trained SLA-risk model available -- run src/ml/train.py first")

    predictions = predict_sla_risk(X, bundle)
    counts = predictions["risk_level"].value_counts()

    buckets = [
        SlaRiskBucket(risk_level=level, case_count=int(counts.get(level, 0)))
        for level in ["LOW", "MEDIUM", "HIGH"]
    ]
    return SlaRiskDistributionResponse(total_cases_scored=len(predictions), buckets=buckets)


@router.post("/interventions", response_model=InterventionCreated, status_code=201,
             dependencies=[Depends(require_role("admin"))])
def create_intervention(body: InterventionCreate):
    """Log an action taken on a flagged case (admin role). Validated against
    config/interventions.yaml; the case must exist; one row per (case, type)."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from src.roi.ledger import InterventionValidationError, assign_case, load_policy, validate_intervention

    policy = load_policy()
    try:
        row = validate_intervention(body.model_dump(), policy)
    except InterventionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    with get_engine().connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM analytics.process_cases WHERE case_id = :c"),
                              {"c": row["case_id"]}).first()
    if not exists:
        raise HTTPException(status_code=404, detail=f"No case found with case_id '{row['case_id']}'")
    # Randomized holdout: the arm depends only on (experiment id, case id), never on risk or on the caller.
    row["assignment"], row["experiment_id"] = assign_case(row["case_id"], policy)
    if row["assignment"] == "holdout":
        row["cost"] = 0.0          # no action is taken on a holdout case; it is logged for its outcome only
    try:
        with get_write_engine().begin() as conn:
            new_id = conn.execute(text(
                "INSERT INTO analytics.interventions (case_id, intervention_type, risk_at_intervention, cost, "
                "breached_after, notes, assignment, experiment_id) VALUES (:case_id, :intervention_type, "
                ":risk_at_intervention, :cost, :breached_after, :notes, :assignment, :experiment_id) "
                "RETURNING intervention_id"), row).scalar()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="This case already has an intervention of that type")
    return InterventionCreated(intervention_id=new_id, case_id=row["case_id"],
                               intervention_type=row["intervention_type"], cost=row["cost"],
                               assignment=row["assignment"], action_required=row["assignment"] == "treat",
                               experiment_id=row["experiment_id"])


@router.get("/roi/summary")
def roi_summary_endpoint():
    """ROI of the intervention ledger, simulated and logged rows reported separately. Effect sizes
    are ASSUMPTIONS (config/interventions.yaml) -- the response says so in `label`."""
    from src.roi.ledger import load_policy, load_sensitivity, roi_summary

    try:
        rows = pd.read_sql(
            "SELECT intervention_type, risk_at_intervention, cost, breached_after, is_simulated, assignment, experiment_id "
            "FROM analytics.interventions", get_engine()).to_dict(orient="records")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"intervention ledger unavailable: {str(exc)[:120]}")
    for r in rows:  # NaN/None -> None for the outcome column
        if r["breached_after"] is None or pd.isna(r["breached_after"]):
            r["breached_after"] = None
    out = roi_summary(rows, load_policy())
    out["sensitivity"] = load_sensitivity()
    return out


_EARLY_RISK_MODEL = None


def _early_risk_model():
    """ONNX ensembles loaded once per process (onnxruntime only; torch is not installed in serving)."""
    global _EARLY_RISK_MODEL
    if _EARLY_RISK_MODEL is None:
        from src.ml.early_risk import EarlyRiskModel

        _EARLY_RISK_MODEL = EarlyRiskModel.load()
    return _EARLY_RISK_MODEL


@router.get("/orders/{case_id}/early-risk", response_model=EarlyRiskResponse)
def order_early_risk(case_id: str, k: int = Query(default=3, ge=2, le=5, description="Events seen so far: 2, 3 or 5")):
    """EARLY-WARNING score from only the first k events of a case (GRU ensemble served through ONNX).

    Not the same as /orders/{id}/risk: that one uses the whole case and is a late-stage triage score. This one
    predicts breach of the training-window p75 cycle-time target (not the configured SLA) and is a weak signal --
    the held-out ROC-AUC of the served model at this k is returned with every response."""
    from src.ml.early_risk import EarlyRiskNotApplicable

    cases = load_cases()
    match = cases[cases["case_id"] == case_id]
    if match.empty:
        raise HTTPException(status_code=404, detail=f"No case found with case_id '{case_id}'")
    try:
        model = _early_risk_model()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="No early-risk model available -- run scripts/export_early_risk.py")
    if k not in model.available_k:
        raise HTTPException(status_code=422, detail=f"k must be one of {model.available_k}")
    try:
        scored = model.score_case(match.iloc[0], load_case_events(case_id), cases, k)
    except EarlyRiskNotApplicable as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    m = model.meta["models"][str(k)]
    return EarlyRiskResponse(
        case_id=case_id, k=k, breach_probability=round(scored["breach_probability"], 4),
        target_definition=model.meta["target"]["definition"], target_hours=round(scored["target_hours"], 1),
        elapsed_hours_at_k=round(scored["elapsed_hours_at_k"], 2),
        test_roc_auc=round(m["test_roc_auc"], 4), test_roc_auc_ci95=[round(x, 4) for x in m["test_roc_auc_ci95"]],
        base_rate=round(m["base_rate"], 4), n_test=m["n_test"],
        model=f"GRU ensemble ({len(m['seeds'])} seeds), ONNX, first-{k}-events",
        warning=(f"EARLY-WARNING, weak signal: held-out ROC-AUC {m['test_roc_auc']:.2f} at k={k} on a ~"
                 f"{m['base_rate']:.0%} base-rate target. Not a late-stage triage score and not the configured SLA."),
    )
