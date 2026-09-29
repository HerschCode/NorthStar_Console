from pydantic import BaseModel
from typing import Literal


class HealthResponse(BaseModel):
    status: str
    database_connected: bool


class CycleTimeResponse(BaseModel):
    mean_hours: float
    median_hours: float
    p90_hours: float
    p99_hours: float
    case_count: int


class CycleTimeSegmentRow(BaseModel):
    segment: str | None
    mean_hours: float
    median_hours: float
    p90_hours: float
    case_count: int


class BottleneckRow(BaseModel):
    stage: str
    avg_hours: float
    median_hours: float
    p90_hours: float
    case_count: int
    pct_of_total_delay: float


class SlaSummaryRow(BaseModel):
    segment: str | None = None
    case_count: int
    breach_count: int
    breach_rate_pct: float


class SupplierScoreRow(BaseModel):
    supplier_id: str
    order_count: int
    avg_cycle_time_hours: float
    cycle_time_std_hours: float | None = None
    sla_breach_rate: float | None = None
    rank_by_cycle_time: float


class FactorExplanation(BaseModel):
    feature: str
    shap_value: float
    value: float


class SlaRiskResponse(BaseModel):
    case_id: str
    breach_probability: float
    risk_level: str
    explanation: list[FactorExplanation] | None = None


class PipelineRunRow(BaseModel):
    run_id: int
    started_at: str
    finished_at: str | None = None
    status: str
    raw_row_count: int | None = None
    cleaned_row_count: int | None = None
    case_count: int | None = None
    error_message: str | None = None


class ConformanceResponse(BaseModel):
    total_cases: int
    conformant_cases: int
    conformance_rate_pct: float
    deviation_breakdown: dict[str, int]


class SlaRiskBucket(BaseModel):
    risk_level: str
    case_count: int


class SlaRiskDistributionResponse(BaseModel):
    total_cases_scored: int
    buckets: list[SlaRiskBucket]


class DataQualityCheck(BaseModel):
    name: str
    status: str  # "pass" | "warn" | "fail"
    value: str
    detail: str | None = None


class DataQualityReport(BaseModel):
    overall: str  # "pass" | "warn" | "fail"
    checked_at: str
    checks: list[DataQualityCheck]


class RiskBucketDrift(BaseModel):
    bucket: str
    baseline_pct: float
    current_pct: float
    delta_pct: float
    status: str  # "stable" | "warn" | "alert"


class PredictionDriftReport(BaseModel):
    status: str  # "stable" | "warn" | "alert" | "no_baseline"
    checked_at: str
    total_current_predictions: int
    baseline_source: str
    buckets: list[RiskBucketDrift]
    note: str | None = None


class FeatureDriftRow(BaseModel):
    feature: str
    psi: float
    status: str  # "stable" | "warn" | "alert"


class FeatureDriftReportSchema(BaseModel):
    status: str  # "stable" | "warn" | "alert" | "no_baseline"
    checked_at: str
    n_current_rows: int
    alert_features: list[str]
    features: list[FeatureDriftRow]
    thresholds: dict[str, float]
    note: str | None = None


class EarlyRiskResponse(BaseModel):
    case_id: str
    k: int                                   # events seen; the case is scored as of its k-th event
    breach_probability: float                # P(cycle time above the p75 target), Platt-calibrated
    target_definition: str
    target_hours: float
    elapsed_hours_at_k: float
    test_roc_auc: float                      # held-out ROC-AUC of the SERVED ONNX ensemble at this k
    test_roc_auc_ci95: list[float]
    base_rate: float
    n_test: int
    model: str
    warning: str


class ApControlException(BaseModel):
    exception_id: int
    case_id: str | None
    control_id: str
    severity: str
    exposure_eur: float | None
    evidence: dict
    supplier_id: str | None = None
    category: str | None = None


class ApControlsSummaryRow(BaseModel):
    control_id: str
    count: int
    total_exposure_eur: float
    severity_breakdown: dict


class ApControlsSummaryResponse(BaseModel):
    label: str
    total_exceptions: int
    controls: list[ApControlsSummaryRow]
    note: str


class WorkingCapitalSummaryResponse(BaseModel):
    dpo_by_supplier: list[dict]
    dpo_by_spend_area: list[dict]
    late_payment: dict
    early_discount_scenario: dict
    assumptions: dict


class InterventionCreate(BaseModel):
    case_id: str
    intervention_type: str | None = None   # defaults to config/interventions.yaml default_type
    risk_at_intervention: float            # model breach probability when the action was taken
    cost: float | None = None              # defaults to the type's configured cost
    breached_after: bool | None = None
    notes: str | None = None


class InterventionCreated(BaseModel):
    intervention_id: int
    case_id: str
    intervention_type: str
    cost: float
    assignment: str = "treat"        # "treat" or "holdout" (randomized; do NOT act on holdout cases)
    action_required: bool = True
    experiment_id: str | None = None


class ErrorResponse(BaseModel):
    detail: str
