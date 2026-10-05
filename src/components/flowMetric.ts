export type FlowMetric = 'median_hours' | 'p75_hours' | 'p90_hours' | 'breach_contribution_pct'
export const FLOW_METRIC_LABEL: Record<FlowMetric, string> = { median_hours: 'Median wait', p75_hours: 'P75 wait', p90_hours: 'P90 wait', breach_contribution_pct: 'Breach contribution' }
