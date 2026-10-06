export interface NavItem { to: string; label: string; keywords?: string }
export interface NavGroup { label: string | null; items: NavItem[] }

export const NAV: NavGroup[] = [
  { label: null, items: [{ to: '/', label: 'Overview', keywords: 'control tower kpi brief' }] },
  { label: 'Operations', items: [
    { to: '/queue', label: 'Action Center', keywords: 'queue risk expected loss work' },
    { to: '/risk-map', label: 'Risk Map', keywords: 'scatter value risk matrix' },
    { to: '/suppliers', label: 'Suppliers', keywords: 'vendor ranking quadrant' },
    { to: '/process', label: 'Process Mining', keywords: 'flow bottleneck variants stages' },
  ] },
  { label: 'Finance', items: [
    { to: '/finance/controls', label: 'AP Controls', keywords: 'duplicate benford three-way match exceptions' },
    { to: '/finance/working-capital', label: 'Working Capital', keywords: 'dpo late payment discount' },
  ] },
  { label: 'Decisions', items: [
    { to: '/interventions', label: 'Interventions', keywords: 'approve propose hold payment' },
    { to: '/roi', label: 'ROI & Uplift', keywords: 'model rules random precision' },
  ] },
  { label: 'Intelligence', items: [
    { to: '/ask', label: 'Ask Northstar', keywords: 'copilot question evidence' },
    { to: '/investigations', label: 'Investigations', keywords: 'report root cause export' },
  ] },
  { label: 'Governance', items: [
    { to: '/security', label: 'AI Security', keywords: 'gateway blocks owasp layers' },
    { to: '/permissions', label: 'Tool Permissions', keywords: 'policy matrix roles' },
    { to: '/approvals', label: 'Approvals', keywords: 'queue human separation of duties' },
    { to: '/attack-lab', label: 'Attack Lab', keywords: 'prompt injection taint scenario' },
    { to: '/audit', label: 'Audit Log', keywords: 'decisions trace history' },
  ] },
  { label: 'Platform', items: [
    { to: '/models', label: 'Model Health', keywords: 'auc calibration drift' },
    { to: '/data-quality', label: 'Data Quality', keywords: 'checks sla coverage' },
    { to: '/lineage', label: 'Lineage', keywords: 'dbt source' },
    { to: '/observability', label: 'Observability', keywords: 'trace latency cost' },
    { to: '/architecture', label: 'Architecture', keywords: 'p1 p2 p3 diagram' },
  ] },
  { label: 'Research', items: [
    { to: '/experiments', label: 'Experiments', keywords: 'failed leaked tied results' },
    { to: '/evidence', label: 'Evidence & Limitations', keywords: 'claims honesty' },
  ] },
]

export const ALL_NAV: NavItem[] = NAV.flatMap((g) => g.items)
