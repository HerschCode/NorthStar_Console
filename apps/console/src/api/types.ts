// Hand-written domain types for the /v1 responses the console reads. The OpenAPI files (openapi/p1|p2|p3.json) describe request
// shapes and paths; P1's responses are free-form JSON, so the shapes below are the contract the console actually depends on.

export type Provenance = 'measured' | 'simulated' | 'experimental' | 'pending' | 'descriptive'

export interface Metric {
  value: number | string | null
  unit: string
  provenance: Provenance
  source: string
  as_of?: string | null
  n?: number
  note?: string
  ci95?: [number, number] | null
  previous?: Metric
  assumption?: Metric
}

export interface SnapshotInfo { live: boolean; reason?: string; as_of?: string; built_for_as_of?: string; built_at?: string }

export interface Overview {
  as_of: string
  model: string
  kpis: Record<string, Metric>
  trend: { series: { month: string; n: number; breach_rate: number; ci95: [number, number] }[]; note: string; source: string; provenance: Provenance }
  funnel: { label: string; n: number }[]
  stage_mix: { stage: string; open_cases: number }[]
  snapshot?: SnapshotInfo
}

export interface Driver { name: string; value: number | null; unit: string; raises_risk: boolean; note?: string }

export interface QueueRow {
  case_id: string; supplier_id: string; category: string; tier: 'CRITICAL' | 'HIGH' | 'ELEVATED' | 'STANDARD' | 'UNSCORED'; status: string
  p_breach: number | null; p_breach_provenance: Provenance; expected_loss_eur: number | null; value_eur: number | null
  stage: string; current_activity: string; elapsed_hours: number; idle_hours: number; target_hours: number; drivers: Driver[]
}
export interface Queue { as_of: string; total_matching: number; limit: number; ranking: Metric; driver_note: string; rows: QueueRow[]; snapshot?: SnapshotInfo }

export interface TimelineEvent { activity: string; stage: string | null; at: string; day: number; gap_hours: number }
export interface Case360 {
  case_id: string; as_of: string; supplier_id: string; category: string; status: string; timeline: TimelineEvent[]; events_seen: number
  longest_wait: TimelineEvent | null; model: { name: string; feature_hash: string }; policy_note: string
  risk?: { p_breach: Metric; expected_loss_eur: Metric; tier: string; value_eur: number; elapsed_hours: number; target_hours: number; idle_hours: number
    model_quality?: { k: number; test_roc_auc: number; ci95: [number, number]; n_test: number; base_rate: number } }
  drivers?: Driver[]; driver_note?: string
  outcome?: { cycle_hours: number; target_hours: number; breached_realistic_target: boolean }
  ap_exceptions?: { control_id: string; severity: string; exposure_eur: number }[]
  snapshot?: SnapshotInfo
}

export interface SupplierRow {
  supplier_id: string; closed_cases: number; breach_rate: Metric; p50_days: number | null; p75_days: number | null; p90_days: number | null
  open_cases: number; open_already_late: number; open_expected_loss_eur: number
}
export interface Suppliers { as_of: string; min_n: number; sort: string; count: number; note: string; rows: SupplierRow[]; snapshot?: SnapshotInfo }
export interface Supplier360 {
  supplier_id: string; as_of: string; closed_cases: Metric; breach_rate: Metric; value_closed_eur: Metric
  cycle_days: { p50: number | null; p75: number | null; p90: number | null; peer_p50: number | null }
  trend: { month: string; n: number; breach_rate: number }[]
  open_cases: { case_id: string; tier: string; p_breach: number; expected_loss_eur: number; value_eur: number; status: string }[]
  open_count: number; ap_exceptions: { control_id: string; severity: string; exposure_eur: number }[]; snapshot?: SnapshotInfo
}

export interface FlowNode { id: string; label: string; cases: number; visits: number; events: number; median_dwell_hours: number | null }
export interface FlowEdge { from: string; to: string; cases: number; transitions: number; median_hours: number | null; p75_hours: number | null; p90_hours: number | null; breach_contribution_pct: number | null; breach_rate_pct: number | null }
export interface Flow { as_of: string; group: string; closed_cases: number; nodes: FlowNode[]; edges: FlowEdge[]; unmapped_activities: string[]; note: string; snapshot?: SnapshotInfo }
export interface Variants { as_of: string; closed_cases: number; variants: { variant: string; cases: number; share_pct: number; breach_rate_pct: number; median_days: number }[] }

export interface RiskMap {
  as_of: string; points: { case_id: string; p_breach: number; value_eur: number; supplier_id: string; stage: string; tier: string }[]
  matrix: { risk_tiers: string[]; value_tiers: string[]; counts: number[][] }
}

export interface ControlHealth { id: string; recall: number | null; recall_ci95?: [number, number]; false_positive_rate_upper_bound?: number; status: 'operational' | 'not_valid'; note?: string }
export interface FinanceControls { controls: ControlHealth[]; external_c4?: { flagged_invoices: number; precision_vs_reversal_proxy: number; lift_over_base: number; note: string }; note: string }
export interface FinanceExceptions { total: number; summary: { control_id: string; severity: string; count: number; exposure_eur: number }[]; exposure_cuts_eur: { p50: number; p90: number } }
export interface WorkingCapital {
  provenance: Provenance; source: string; available?: boolean
  late_payment: { n_cases: number; n_late: number; late_rate_pct: number; exposure_eur: number; payment_terms_days: number }
  early_discount_scenario: { n_eligible: number; n_captured: number; captured_discount_eur: number; potential_discount_eur: number; missed_discount_eur: number; discount_pct: number; discount_window_days: number }
  dpo_by_spend_area: { sub_spend_area: string; case_count: number; mean_days: number; median_days: number }[]
}
export interface Roi {
  logged: Metric
  simulated: { provenance: Provenance; assumptions: { breach_cost: number; capacity_pct: number; types: Record<string, { cost: number; effect: number }> } }
  model_vs_rules: { provenance: Provenance; precision_at_k: Record<string, Record<string, { precision: number; ci95: [number, number] }>>; n_test: number; verdict: string } | null
}
export interface Models {
  late_stage_model: { available: boolean; name: string; trained_at: string; calibration: string; sklearn_version: string; feature_code_sha256: string; served: Record<string, number>; validation: string[] }
  early_warning: { name: string; target: string; trained_at: string; per_k: Record<string, { test_roc_auc: number; ci95: [number, number]; n_test: number; base_rate: number }>; note: string }
  governance_checks: { check: string; status: string }[]
}
export interface RegistryModel { version: string; registered_at: string; sha256: string; data_fingerprint: string | null; metrics: Record<string, unknown>; notes: string }
export interface RegistryStatus { available: boolean; name: string; champion: RegistryModel | null; challenger: RegistryModel | null; versions: string[]; champion_hash_verified: boolean | null; events: { at: string; event: string; detail: Record<string, unknown> }[]; note: string }
export interface DataQuality { cases: number; events: number; suppliers: number; explicit_sla_share_pct: number; mode: string; freshness_note: string; checks: { check: string; status: string; detail?: string }[] }
export interface Experiments { experiments: { id: string; name: string; hypothesis: string; result: string; decision: string; detail: string; doc: string }[]; note: string }
export interface Evidence { claims: { claim: string; status: string; evidence: string }[]; experiments: unknown[]; limitations: string[] }
export interface Lineage { graph: { nodes: { id: string; label: string; layer: number }[]; edges: [string, string][] }; chains: Record<string, string[] | null> }
export interface Briefing { as_of: string; facts: { id: string; fact: string; metric: string }[] }

// P2 via P3
export interface LimitModel { provider: string; model: string; scope: string; retry_after_s: number | null; message: string; local_cap?: boolean }
export interface LimitsInfo { exhausted: boolean; retry_after_s: number | null; models: LimitModel[]; other: string[] }
export interface ModelStatus { provider: string; model: string; state: 'ok' | 'cooling' | 'no_key' | 'unavailable'; cooldown_scope?: string | null; retry_after_s?: number | null; used_minute?: number; used_day?: number; tokens_minute?: number; caps?: { rpm: number | null; rpd: number | null; tpm: number | null }; note?: string }
export interface Limits {
  gateway: { per_user_daily: number; global_daily: number; used_by_you: number | null; used_overall: number; resets_in_s: number }
  assistant: { mode: string; summary: string; next_available_s: number | null; models: ModelStatus[]; notes: string[] }
}
export interface GatewayVerdict { gateway_latency_ms?: number; decision: 'allow' | 'block'; reason?: string | null; phase?: string | null; layers: Record<string, { decision: string; latency_ms: number }>; pii_found?: unknown; latency_ms: number; trace_id: string; note?: string }
export interface Claim { text: string; supported: boolean; evidence_ids: string[]; reason: string }
export interface EvidenceItem { id: string; type: 'p1_metric' | 'policy'; endpoint?: string; label?: string; value?: number | string; unit?: string; provenance?: string; retrieved_at?: string; doc_id?: string; title?: string; section?: string | null; version?: string | null; citation?: string; excerpt?: string }
export interface AskResponse {
  supported_claims?: number; total_claims?: number
  blocked: boolean; gateway: GatewayVerdict; answer: string | null; abstained?: boolean; abstain_reason?: string; claims?: Claim[]; evidence?: EvidenceItem[]
  limits?: LimitsInfo | null
  actions_suggested?: { tool: string; case_id: string; intervention_type: string; rationale: string }[]; trace_id?: string; cost_usd?: number; latency_ms?: number; model?: string; notes?: string[]
}
export interface Investigation {
  id: string; created: number; question: string; model: string; summary: string; root_causes: (Claim & { causal_language?: boolean })[]; recommendations: { text: string; evidence_ids: string[]; supported?: boolean; action: unknown }[]
  limitations: string; limits?: LimitsInfo | null; evidence: { data: EvidenceItem[]; documents: EvidenceItem[] }; relevant_policy: string[]; warnings: string[]; trace_id: string; gateway?: GatewayVerdict; blocked?: boolean
}
export interface BriefItem { title: string; sentences: { text: string; fact_ids: string[] }[] }
export interface BriefOut {
  limits?: LimitsInfo | null; as_of: string; items: BriefItem[]; source: string; model: string; label?: string; facts: { id: string; fact: string }[]; gateway?: GatewayVerdict }
export interface NLFilterOut {
  limits?: LimitsInfo | null; rejected: boolean; reason?: string; target?: 'queue' | 'suppliers'; endpoint?: string; filter?: Record<string, unknown>; restatement?: string; source?: string; gateway?: GatewayVerdict; blocked?: boolean }
export interface InterventionRow {
  id: number; case_id: string; intervention_type: string; rationale: string; risk: number; proposer: string; proposer_role: string; status: string; approval_id: string | null
  gateway_decision: { effect?: string; stage?: string; reasons?: string[]; risk?: string }; p1_intervention_id: number | null; assignment: string | null; outcome: string | null; created: number; updated: number
  history: { ts: number; status: string; actor: string; detail: Record<string, unknown> }[]
}

// P3
export interface Me { authenticated: boolean; user_id?: string; role?: string; source?: string; label?: string; demo_mode?: boolean; roles?: string[] }
export interface Login { token: string; user_id: string; role: string; expires_at: number; label: string }
export interface GovSummary {
  window: string; requests: number; blocked_requests: number; block_rate: number | null; blocks_by_layer: Record<string, number>; blocks_by_rule: Record<string, number>
  pii_requests: number; latency_ms: { p50: number | null; p95: number | null }; actions: Record<string, number>; series: { t: number; requests: number; blocked: number }[]
}
export interface GovEvent { kind: 'text' | 'action'; ts: number; request_id?: string; phase?: string; decision?: string; layer?: string | null; rule_id?: string | null; latency_ms?: number; session: string; trace_id?: string | null; tool?: string; effect?: string; stage?: string | null; rule?: string | null; risk?: string; role?: string; approval_id?: string | null; reasons?: string[] }
export interface PolicyMatrix { roles: string[]; tools: { tool: string; kind: string; output_trust: string; taint: Record<string, string>; max_per_session: number | null; cells: Record<string, { effect: 'allow' | 'approval' | 'deny'; rules: { name: string; approval: string; require_role_separation: boolean; args: Record<string, string> }[] }> }[]; policy_sha256: string }
export interface RuleRow { rule_id: string; name: string; layer: string; owasp: string; atlas: string | null; atlas_verified: boolean; redteam?: string | null; kind: string }
export interface GwConfig { detector_backend: Record<string, unknown>; thresholds: Record<string, number>; policy: { sha256: string; default_effect: string }; model_integrity: { mode: string; artifact_hashes: Record<string, string> }; demo_mode: boolean; assistant_configured: boolean }
export interface Approval { id: string; created_at: number; status: string; requester_role: string; requester_id: string; tool: string; args: Record<string, unknown>; reasons: string[]; evidence: { tainted?: unknown[]; policy_rule?: string }; risk: string; require_role_separation: boolean; decided_by?: string | null; decided_role?: string | null; note?: string; expired?: boolean }
export interface LabScenario { id: string; category: string; title: string; kind: 'text' | 'action'; curated: boolean; harmful: boolean; evasion?: boolean; owasp?: string; redteam?: string | null }
export interface LabStep { step: string; layer?: string; decision: string; detail?: string; stage?: string | null; tool?: string; reasons?: string[]; risk?: string; latency_ms?: number; harmful?: boolean }
export interface LabRun { scenario_id: string; kind: string; defenses: string; target: string; title: string; timeline: LabStep[]; tools_executed: string[]; compromised: boolean; outcome: string; latency_ms: number; note: string }
export interface TraceOut { trace_id: string; gateway: { decisions: { ts: number; request_id: string; phase: string; decision: string; layer: string | null; rule_id: string | null; latency_ms: number }[]; actions: { ts: number; tool: string; effect: string; stage: string | null; rule: string | null }[] }; assistant: { total_ms: number; cost_usd: number; spans: { name: string; kind: string; start_ms: number; duration_ms: number; attrs: Record<string, unknown>; status: string }[] } | null }
export interface Services { gateway: { status: string; demo_mode: boolean }; assistant: { configured: boolean; reachable: boolean; detail: string } }
