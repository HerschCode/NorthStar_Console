import { createRootRoute, createRoute, createRouter, lazyRouteComponent, redirect } from '@tanstack/react-router'
import { Shell } from './components/Shell'
import { LoadingBlock } from './components/ui'

// Every page is its own chunk (and ECharts is loaded only when a chart renders), so the first paint stays small.
const Overview = lazyRouteComponent(() => import('./pages/Overview'), 'Overview')
const ActionCenter = lazyRouteComponent(() => import('./pages/Operations'), 'ActionCenter')
const Case360 = lazyRouteComponent(() => import('./pages/Operations'), 'Case360')
const RiskMap = lazyRouteComponent(() => import('./pages/Operations'), 'RiskMap')
const Suppliers = lazyRouteComponent(() => import('./pages/Operations'), 'Suppliers')
const Supplier360 = lazyRouteComponent(() => import('./pages/Operations'), 'Supplier360')
const ProcessMining = lazyRouteComponent(() => import('./pages/Operations'), 'ProcessMining')
const ApControls = lazyRouteComponent(() => import('./pages/Finance'), 'ApControls')
const WorkingCapital = lazyRouteComponent(() => import('./pages/Finance'), 'WorkingCapital')
const Interventions = lazyRouteComponent(() => import('./pages/Decisions'), 'Interventions')
const Roi = lazyRouteComponent(() => import('./pages/Decisions'), 'Roi')
const Ask = lazyRouteComponent(() => import('./pages/Intelligence'), 'Ask')
const Investigations = lazyRouteComponent(() => import('./pages/Intelligence'), 'Investigations')
const Security = lazyRouteComponent(() => import('./pages/Governance'), 'Security')
const Permissions = lazyRouteComponent(() => import('./pages/Governance'), 'Permissions')
const Approvals = lazyRouteComponent(() => import('./pages/Governance'), 'Approvals')
const AttackLab = lazyRouteComponent(() => import('./pages/Governance'), 'AttackLab')
const AuditLog = lazyRouteComponent(() => import('./pages/Governance'), 'AuditLog')
const Observability = lazyRouteComponent(() => import('./pages/Platform'), 'Observability')
const Models = lazyRouteComponent(() => import('./pages/Platform'), 'Models')
const DataQuality = lazyRouteComponent(() => import('./pages/Platform'), 'DataQuality')
const Lineage = lazyRouteComponent(() => import('./pages/Platform'), 'Lineage')
const Architecture = lazyRouteComponent(() => import('./pages/Platform'), 'Architecture')
const Experiments = lazyRouteComponent(() => import('./pages/Platform'), 'Experiments')
const EvidencePage = lazyRouteComponent(() => import('./pages/Platform'), 'EvidencePage')
const Alerts = lazyRouteComponent(() => import('./pages/Platform'), 'Alerts')
const NotFound = lazyRouteComponent(() => import('./pages/Platform'), 'NotFound')

const root = createRootRoute({ component: Shell, notFoundComponent: NotFound })
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const r = (path: string, component: any, extra: object = {}) => createRoute({ getParentRoute: () => root, path, component, pendingComponent: () => <LoadingBlock label="Loading page" />, ...extra })
const qSearch = { validateSearch: (s: Record<string, unknown>): { q?: string } => ({ q: typeof s.q === 'string' ? s.q : undefined }) }

const routeTree = root.addChildren([
  r('/', Overview),
  r('/queue', ActionCenter),
  r('/cases/$caseId', Case360),
  r('/risk-map', RiskMap),
  r('/suppliers', Suppliers),
  r('/suppliers/$supplierId', Supplier360),
  r('/process', ProcessMining),
  r('/finance/controls', ApControls),
  r('/finance/working-capital', WorkingCapital),
  r('/finance', () => null, { beforeLoad: () => { throw redirect({ to: '/finance/controls' }) } }),
  r('/interventions', Interventions),
  r('/roi', Roi),
  r('/ask', Ask, qSearch),
  r('/investigations', Investigations, qSearch),
  r('/security', Security),
  r('/permissions', Permissions),
  r('/approvals', Approvals),
  r('/attack-lab', AttackLab),
  r('/audit', AuditLog),
  r('/observability', Observability, { validateSearch: (s: Record<string, unknown>): { trace?: string } => ({ trace: typeof s.trace === 'string' && s.trace ? s.trace : undefined }) }),
  r('/models', Models),
  r('/data-quality', DataQuality),
  r('/lineage', Lineage),
  r('/architecture', Architecture),
  r('/experiments', Experiments),
  r('/evidence', EvidencePage),
  r('/alerts', Alerts),
])

export const router = createRouter({ routeTree, defaultPreload: 'intent', scrollRestoration: true })
