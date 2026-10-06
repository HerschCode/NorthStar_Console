import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { p1, p3, unwrap, newTraceparent } from './client'
import { useSession } from '../state/session'
import type * as T from './types'

const STALE = 60_000

/** Every P1 read is keyed by the replay clock, so changing it refetches everything that depends on it. */
function useAsOf(): string | undefined {
  return useSession().asOf ?? undefined
}

// ── P1 reads ──
export const useOverview = () => {
  const as_of = useAsOf()
  return useQuery({ queryKey: ['overview', as_of], staleTime: STALE, queryFn: () => unwrap<T.Overview>(p1.GET('/v1/overview', { params: { query: { as_of } } })) })
}

export interface QueueFilters { min_value?: number; supplier?: string; stage?: string; limit?: number }
export const useQueue = (f: QueueFilters = {}) => {
  const as_of = useAsOf()
  return useQuery({ queryKey: ['queue', as_of, f], staleTime: STALE, queryFn: () => unwrap<T.Queue>(p1.GET('/v1/queue', { params: { query: { as_of, limit: 100, ...f } } })) })
}

export const useCase = (id: string) => {
  const as_of = useAsOf()
  return useQuery({ queryKey: ['case', id, as_of], staleTime: STALE, queryFn: () => unwrap<T.Case360>(p1.GET('/v1/cases/{case_id}', { params: { path: { case_id: id }, query: { as_of } } })) })
}

export const useSuppliers = (sort = 'ci_lower', min_n = 20) => {
  const as_of = useAsOf()
  return useQuery({ queryKey: ['suppliers', as_of, sort, min_n], staleTime: STALE, queryFn: () => unwrap<T.Suppliers>(p1.GET('/v1/suppliers', { params: { query: { as_of, sort, min_n } } })) })
}

export const useSupplier = (id: string) => {
  const as_of = useAsOf()
  return useQuery({ queryKey: ['supplier', id, as_of], staleTime: STALE, queryFn: () => unwrap<T.Supplier360>(p1.GET('/v1/suppliers/{supplier_id}', { params: { path: { supplier_id: id }, query: { as_of } } })) })
}

export const useFlow = (group: 'stage' | 'activity') => {
  const as_of = useAsOf()
  return useQuery({ queryKey: ['flow', as_of, group], staleTime: STALE, queryFn: () => unwrap<T.Flow>(p1.GET('/v1/process/flow', { params: { query: { as_of, group } } })) })
}
export const useVariants = () => {
  const as_of = useAsOf()
  return useQuery({ queryKey: ['variants', as_of], staleTime: STALE, queryFn: () => unwrap<T.Variants>(p1.GET('/v1/process/variants', { params: { query: { as_of } } })) })
}
export const useRiskMap = () => {
  const as_of = useAsOf()
  return useQuery({ queryKey: ['riskmap', as_of], staleTime: STALE, queryFn: () => unwrap<T.RiskMap>(p1.GET('/v1/risk-map', { params: { query: { as_of } } })) })
}
export const useFinanceControls = () => useQuery({ queryKey: ['fin-controls'], staleTime: STALE, queryFn: () => unwrap<T.FinanceControls>(p1.GET('/v1/finance/controls', { params: { query: {} } })) })
export const useFinanceExceptions = () => useQuery({ queryKey: ['fin-exc'], staleTime: STALE, queryFn: () => unwrap<T.FinanceExceptions>(p1.GET('/v1/finance/exceptions', { params: { query: {} } })) })
export const useWorkingCapital = () => useQuery({ queryKey: ['wc'], staleTime: STALE, queryFn: () => unwrap<T.WorkingCapital>(p1.GET('/v1/finance/working-capital')) })
export const useRoi = () => useQuery({ queryKey: ['roi'], staleTime: STALE, queryFn: () => unwrap<T.Roi>(p1.GET('/v1/interventions/roi')) })
export const useModels = () => useQuery({ queryKey: ['models'], staleTime: STALE, queryFn: () => unwrap<T.Models>(p1.GET('/v1/models')) })
export const useRegistry = () => useQuery({ queryKey: ['registry'], staleTime: STALE, queryFn: () => unwrap<T.RegistryStatus>(p1.GET('/v1/mlops/registry', { params: { query: {} } })) })
export const useDataQuality = () => useQuery({ queryKey: ['dq'], staleTime: STALE, queryFn: () => unwrap<T.DataQuality>(p1.GET('/v1/data-quality')) })
export const useLineage = () => useQuery({ queryKey: ['lineage'], staleTime: STALE, queryFn: () => unwrap<T.Lineage>(p1.GET('/v1/lineage', { params: { query: {} } })) })
export const useExperiments = () => useQuery({ queryKey: ['experiments'], staleTime: STALE, queryFn: () => unwrap<T.Experiments>(p1.GET('/v1/experiments')) })
export const useEvidence = () => useQuery({ queryKey: ['evidence'], staleTime: STALE, queryFn: () => unwrap<T.Evidence>(p1.GET('/v1/evidence')) })
export const useSearch = (q: string) => useQuery({
  queryKey: ['search', q], enabled: q.trim().length >= 2, staleTime: STALE,
  queryFn: () => unwrap<{ results: { type: string; id: string; label: string }[] }>(p1.GET('/v1/search', { params: { query: { q, limit: 12 } } })),
})

// ── P3 reads ──
export const useMe = () => useQuery({ queryKey: ['me'], staleTime: 30_000, retry: false, queryFn: () => unwrap<T.Me>(p3.GET('/v1/me'), 'p3') })
export const useGovSummary = (window: string) => useQuery({ queryKey: ['gov-summary', window], refetchInterval: 15_000, queryFn: () => unwrap<T.GovSummary>(p3.GET('/v1/governance/summary', { params: { query: { window } } }), 'p3') })
export const useGovEvents = (limit = 100) => {
  const { identity } = useSession()
  return useQuery({ queryKey: ['gov-events', limit, identity?.role], refetchInterval: 15_000, queryFn: () => unwrap<{ detail_level: string; note: string | null; events: T.GovEvent[] }>(p3.GET('/v1/governance/events', { params: { query: { limit } } }), 'p3') })
}
export const usePolicyMatrix = () => useQuery({ queryKey: ['policy-matrix'], staleTime: STALE, queryFn: () => unwrap<T.PolicyMatrix>(p3.GET('/v1/governance/policy-matrix'), 'p3') })
export const useRules = () => useQuery({ queryKey: ['rules'], staleTime: STALE, queryFn: () => unwrap<{ rules: T.RuleRow[]; note: string }>(p3.GET('/v1/rules'), 'p3') })
export const useGwConfig = () => useQuery({ queryKey: ['gw-config'], staleTime: STALE, queryFn: () => unwrap<T.GwConfig>(p3.GET('/v1/config'), 'p3') })
export const useLimits = () => useQuery({ queryKey: ['limits'], refetchInterval: 20_000, retry: false, queryFn: () => unwrap<T.Limits>(p3.GET('/v1/limits'), 'p3') })
export const useServices = () => useQuery({ queryKey: ['services'], refetchInterval: 20_000, queryFn: () => unwrap<T.Services>(p3.GET('/v1/services'), 'p3') })
export const useLabScenarios = () => useQuery({ queryKey: ['lab-scenarios'], staleTime: STALE, queryFn: () => unwrap<{ scenarios: T.LabScenario[]; note: string }>(p3.GET('/v1/lab/scenarios'), 'p3') })
export const useApprovals = (status?: 'pending' | 'approved' | 'denied') => {
  const { identity } = useSession()
  return useQuery({
    queryKey: ['approvals', status, identity?.role], enabled: !!identity && ['manager', 'admin', 'finance'].includes(identity.role), refetchInterval: 10_000,
    queryFn: () => unwrap<{ approvals: T.Approval[] }>(p3.GET('/v1/approvals', { params: { query: { status } } }), 'p3'),
  })
}
export const useInterventions = () => {
  const { identity } = useSession()
  return useQuery({ queryKey: ['interventions', identity?.userId], enabled: !!identity, refetchInterval: 10_000,
    queryFn: () => unwrap<{ interventions: T.InterventionRow[] }>(p3.GET('/v1/interventions', { params: { query: {} } }), 'p3') })
}
export const useInvestigations = () => {
  const { identity } = useSession()
  return useQuery({ queryKey: ['investigations'], enabled: !!identity, queryFn: () => unwrap<{ investigations: { id: string; created: number; question: string; summary: string; model: string }[] }>(p3.GET('/v1/investigations', { params: { query: {} } }), 'p3') })
}
export const useTrace = (traceId: string | null) => useQuery({ queryKey: ['trace', traceId], enabled: !!traceId, retry: false, queryFn: () => unwrap<T.TraceOut>(p3.GET('/v1/traces/{trace_id}', { params: { path: { trace_id: traceId! } } }), 'p3') })
export const useBriefingFacts = () => {
  const as_of = useAsOf()
  return useQuery({ queryKey: ['briefing-facts', as_of], staleTime: STALE, queryFn: () => unwrap<T.Briefing>(p1.GET('/v1/briefing', { params: { query: { as_of } } })) })
}

// ── P3 writes (AI + actions all go through the gateway) ──
export interface AskContext { page?: string; case_id?: string; supplier_id?: string; control?: string; as_of?: string }
export function useAsk() {
  const as_of = useAsOf()
  return useMutation({ mutationFn: (v: { question: string; context?: AskContext }) => unwrap<T.AskResponse>(p3.POST('/v1/ask', { body: { question: v.question, context: { as_of, ...v.context } } }), 'p3') })
}
export function useInvestigate() {
  const qc = useQueryClient()
  const as_of = useAsOf()
  return useMutation({
    mutationFn: (v: { question: string; context?: AskContext }) => unwrap<T.Investigation>(p3.POST('/v1/investigations', { body: { question: v.question, context: { as_of, ...v.context } } }), 'p3'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['investigations'] }),
  })
}
export function useBrief() {
  const as_of = useAsOf()
  return useMutation({ mutationFn: () => unwrap<T.BriefOut>(p3.POST('/v1/briefing', { body: { as_of: as_of ?? null } }), 'p3') })
}
export const useNLFilter = () => useMutation({ mutationFn: (question: string) => unwrap<T.NLFilterOut>(p3.POST('/v1/nl-filter', { body: { question } }), 'p3') })

export function usePropose() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { case_id: string; intervention_type: string; rationale: string; risk: number; sources?: { kind: string; text: string; trust: 'trusted' | 'untrusted' }[] }) =>
      unwrap<T.InterventionRow>(p3.POST('/v1/interventions', { body: v as never }), 'p3'),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['interventions'] }); qc.invalidateQueries({ queryKey: ['approvals'] }); qc.invalidateQueries({ queryKey: ['gov-events'] }) },
  })
}
export function useDecideIntervention() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: number; verb: 'approve' | 'reject' | 'outcome'; body?: Record<string, unknown> }) =>
      unwrap<T.InterventionRow>(p3.POST('/v1/interventions/{iid}/{verb}', { params: { path: { iid: v.id, verb: v.verb } }, body: (v.body ?? {}) as never }), 'p3'),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['interventions'] }); qc.invalidateQueries({ queryKey: ['approvals'] }); qc.invalidateQueries({ queryKey: ['gov-events'] }); qc.invalidateQueries({ queryKey: ['overview'] }) },
  })
}
export function useLabRun() {
  return useMutation({ mutationFn: (v: { scenario_id: string; defenses: 'on' | 'off'; target: 'stub' | 'p2' }) => unwrap<T.LabRun>(p3.POST('/v1/lab/run', { body: v }), 'p3') })
}
export function useLogin() {
  return useMutation({ mutationFn: (role: string) => unwrap<T.Login>(p3.POST('/v1/demo/login', { body: { role } }), 'p3') })
}

export { newTraceparent }
