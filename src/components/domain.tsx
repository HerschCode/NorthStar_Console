import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import type { AskResponse, Claim, EvidenceItem, GatewayVerdict, Metric, TimelineEvent } from '../api/types'
import { Badge, Button, Callout, Card, cx, ErrorState } from './ui'
import { MetricValue, ProvBadge } from './Prov'
import { eur, fmt, hours, withUnit } from '../lib/format'
import { useAsk, usePropose, useRoi } from '../api/hooks'
import { useSession } from '../state/session'
import { ApiError } from '../api/client'

export function Kpi({ label, metric, hint, to, fmtValue }: { label: string; metric: Metric | undefined; hint?: ReactNode; to?: string; fmtValue?: (v: Metric['value']) => string }) {
  const body = (
    <div className="h-full rounded-lg border border-line bg-panel p-3 shadow-sm">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold leading-tight"><MetricValue m={metric} fmtValue={fmtValue} className="items-baseline" /></div>
      {metric?.previous && metric.previous.value !== null && metric.value !== null && (
        <div className="mt-1 text-xs text-muted">{Number(metric.value) - Number(metric.previous.value) >= 0 ? '▲' : '▼'} {fmt(Math.abs(Number(metric.value) - Number(metric.previous.value)), 1)} pts vs previous period ({withUnit(metric.previous.value, metric.previous.unit)}, n = {metric.previous.n})</div>
      )}
      {metric?.value === null && metric?.note && <div className="mt-1 text-xs text-muted">{metric.note}</div>}
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  )
  return to ? <Link to={to} className="block no-underline text-ink focus-visible:outline-2">{body}</Link> : body
}

export function TierBadge({ tier }: { tier: string }) {
  const t = tier === 'CRITICAL' ? 'bad' : tier === 'HIGH' ? 'warn' : tier === 'ELEVATED' ? 'info' : 'neutral'
  return <Badge tone={t}>{tier}</Badge>
}

export function StatusBadge({ status }: { status: string }) {
  const tone = ['executed', 'approved', 'outcome_recorded', 'pass', 'operational', 'allow'].includes(status) ? 'good'
    : ['gateway_held', 'warn', 'pending', 'require_approval', 'held'].includes(status) ? 'warn'
    : ['gateway_denied', 'rejected', 'execution_failed', 'fail', 'deny', 'block', 'not_valid'].includes(status) ? 'bad' : 'neutral'
  return <Badge tone={tone}>{status.replace(/_/g, ' ')}</Badge>
}

export function GatewayChip({ g }: { g: GatewayVerdict | undefined }) {
  if (!g) return null
  const blocked = g.decision === 'block'
  const layers = Object.entries(g.layers ?? {})
  return (
    <div data-testid="gateway-chip" className={cx('rounded-md border p-2 text-xs', blocked ? 'border-bad/40 bg-bad-soft' : 'border-good/40 bg-good-soft')}>
      <div className="flex flex-wrap items-center gap-2">
        <strong>{blocked ? 'Blocked by the AI gateway' : 'Passed the AI gateway'}</strong>
        {g.reason && <span className="text-muted">{g.reason}</span>}
        <span className="text-muted">· {fmt(g.gateway_latency_ms as number | undefined ?? g.latency_ms, 0)} ms</span>
        {g.note && <span className="text-muted">· {g.note}</span>}
      </div>
      {layers.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-1.5">
          {layers.map(([name, l]) => <li key={name}><Badge tone={l.decision === 'block' ? 'bad' : l.decision === 'pass' ? 'good' : 'neutral'}>{name.replace(/_/g, ' ')}: {l.decision}</Badge></li>)}
        </ul>
      )}
    </div>
  )
}

export function Timeline({ events, slowAfterHours = 240 }: { events: TimelineEvent[]; slowAfterHours?: number }) {
  return (
    <ol className="relative ml-2 border-l-2 border-line pl-4" aria-label="Event timeline">
      {events.map((e, i) => (
        <li key={i} className="relative py-1 text-sm">
          <span aria-hidden className={cx('absolute -left-[22px] top-2.5 h-2.5 w-2.5 rounded-full border-2 border-panel', e.gap_hours > slowAfterHours ? 'bg-bad' : 'bg-accent')} />
          <span className="font-medium">{e.activity}</span>
          {e.stage && <span className="ml-1 text-xs text-muted">({e.stage})</span>}
          <span className="ml-2 text-xs text-muted">day {e.day}{e.gap_hours > 0 ? ` · +${hours(e.gap_hours)}` : ''} · {e.at.slice(0, 16).replace('T', ' ')}</span>
          {e.gap_hours > slowAfterHours && <Badge tone="bad">long wait</Badge>}
        </li>
      ))}
    </ol>
  )
}

export function EvidenceDrawer({ items, highlight, onClose }: { items: EvidenceItem[]; highlight?: string[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { ref.current?.focus() }, [])
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Evidence" onKeyDown={(e) => e.key === 'Escape' && onClose()} className="h-full w-full max-w-md overflow-y-auto border-l border-line bg-panel p-4 shadow-xl">
        <div className="mb-3 flex items-center justify-between"><h2 className="text-lg font-semibold">Evidence</h2><Button variant="ghost" onClick={onClose} aria-label="Close evidence">✕</Button></div>
        <ul className="space-y-3">
          {items.map((e) => (
            <li key={e.id} data-testid={`evidence-${e.id}`} className={cx('rounded-md border p-3 text-sm', highlight?.includes(e.id) ? 'border-accent bg-accent-soft' : 'border-line')}>
              <div className="flex items-center justify-between"><span className="font-mono text-xs">{e.id}</span><Badge tone={e.type === 'policy' ? 'info' : 'neutral'}>{e.type === 'policy' ? 'policy document' : 'live data (P1)'}</Badge></div>
              {e.type === 'p1_metric' ? (
                <>
                  <div className="mt-1 font-medium">{e.label}</div>
                  <div className="text-lg tabular-nums">{String(e.value)} <span className="text-sm text-muted">{e.unit}</span> {e.provenance && <ProvBadge p={e.provenance as never} />}</div>
                  <div className="mt-1 text-xs text-muted">endpoint <code>{e.endpoint}</code><br />retrieved {e.retrieved_at}</div>
                </>
              ) : (
                <>
                  <div className="mt-1 font-medium">{e.citation ?? e.title}</div>
                  <blockquote className="mt-1 border-l-2 border-line pl-2 text-xs text-muted">{e.excerpt}</blockquote>
                  <div className="mt-1 text-xs text-muted">document <code>{e.doc_id}</code> · section {e.section ?? '—'} · version {e.version ?? 'not versioned'}</div>
                </>
              )}
            </li>
          ))}
          {items.length === 0 && <li className="text-sm text-muted">No evidence was retrieved.</li>}
        </ul>
      </div>
    </div>
  )
}

export function ClaimList({ claims, onEvidence }: { claims: Claim[]; onEvidence: (ids: string[]) => void }) {
  if (!claims.length) return <p className="text-sm text-muted">No verifiable claims were returned.</p>
  return (
    <ul className="space-y-1.5" aria-label="Claims and their support">
      {claims.map((c, i) => (
        <li key={i} className="flex items-start gap-2 text-sm" data-supported={c.supported}>
          <span aria-label={c.supported ? 'supported' : 'unsupported'} className={cx('mt-0.5 inline-block w-4 shrink-0 text-center font-bold', c.supported ? 'text-good' : 'text-bad')}>{c.supported ? '✓' : '✕'}</span>
          <span className="min-w-0">
            <span className={c.supported ? '' : 'text-bad'}>{c.text}</span>{' '}
            {c.evidence_ids.length > 0 && <button type="button" onClick={() => onEvidence(c.evidence_ids)} className="rounded border border-line px-1 text-[11px] text-accent hover:bg-accent-soft">{c.evidence_ids.join(', ')}</button>}
            {!c.supported && <span className="block text-xs text-muted">Not verified: {c.reason}</span>}
          </span>
        </li>
      ))}
    </ul>
  )
}

const TYPE_LABEL: Record<string, string> = { expedite_approval: 'Expedite approval', supplier_escalation: 'Supplier escalation', reassign_owner: 'Reassign owner' }

/** Propose-intervention flow: simulated ROI (labelled), then the gateway's decision (held / denied / allowed) with its checks. */
export function ProposeDialog({ caseId, risk, rationale, type, onClose }: { caseId: string; risk: number; rationale: string; type?: string; onClose: () => void }) {
  const { identity } = useSession()
  const roi = useRoi()
  const propose = usePropose()
  const [t, setT] = useState(type ?? 'expedite_approval')
  const [why, setWhy] = useState(rationale)
  const types = roi.data?.simulated.assumptions.types ?? {}
  const a = types[t]
  const breachCost = roi.data?.simulated.assumptions.breach_cost ?? 0
  const net = a ? a.effect * breachCost - a.cost : null
  const row = propose.data
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" aria-label="Propose intervention" className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border border-line bg-panel p-5 shadow-xl">
        <h2 className="text-lg font-semibold">Propose intervention — case {caseId}</h2>
        {!identity && <Callout tone="warn" title="Sign in first">Use the “Sign in (demo)” menu in the top bar. Proposals need an identity so the gateway can enforce who may do what.</Callout>}
        {!row && (
          <>
            <label className="mt-3 block text-xs text-muted" htmlFor="ptype">Intervention type</label>
            <select id="ptype" value={t} onChange={(e) => setT(e.target.value)} className="mt-1 w-full rounded-md border border-line bg-panel2 px-2 py-1.5">
              {Object.keys(types).length ? Object.keys(types).map((k) => <option key={k} value={k}>{TYPE_LABEL[k] ?? k}</option>) : <option value={t}>{TYPE_LABEL[t] ?? t}</option>}
            </select>
            <label className="mt-3 block text-xs text-muted" htmlFor="pwhy">Rationale (evidence for the approver)</label>
            <textarea id="pwhy" value={why} onChange={(e) => setWhy(e.target.value)} rows={3} maxLength={400} className="mt-1 w-full rounded-md border border-line bg-panel2 px-2 py-1.5" />
            <div className="mt-3 rounded-md border border-line bg-panel2 p-3 text-sm">
              <div className="flex items-center gap-2"><strong>Simulated value</strong><ProvBadge p="simulated" source="config/interventions.yaml" note="Assumed cost and effect; no real intervention outcomes exist." /></div>
              {a ? <p className="mt-1">Assumed cost {eur(a.cost)}, assumed {fmt(100 * a.effect, 0)}% cut in breach probability, avoided-breach value {eur(breachCost)} → net <strong className={cx((net ?? 0) >= 0 ? 'text-good' : 'text-bad')}>{eur(net)}</strong> if the case would otherwise breach.</p> : <p className="mt-1 text-muted">Assumptions unavailable.</p>}
            </div>
            <p className="mt-2 text-xs text-muted">Approval is decided by a human through the AI gateway's action firewall (policy → taint → approval). The proposer can never approve their own request.</p>
          </>
        )}
        {propose.isError && <div className="mt-3"><ErrorState error={propose.error} /></div>}
        {row && (
          <div className="mt-3 space-y-2" data-testid="propose-result">
            <div className="flex items-center gap-2"><StatusBadge status={row.status} /><span className="text-sm">Intervention #{row.id}</span></div>
            <div className="rounded-md border border-line p-3 text-sm">
              <div className="font-semibold">Gateway security checks</div>
              <ul className="mt-1 list-disc pl-5 text-muted">
                <li>Policy and role: {row.gateway_decision?.effect === 'deny' && row.gateway_decision?.stage === 'policy' ? 'denied' : 'permitted'}</li>
                <li>Taint check (argument copied from untrusted content): {row.gateway_decision?.stage === 'taint' ? 'denied' : 'passed'}</li>
                <li>Human approval: {row.status === 'gateway_held' ? 'required — waiting for a different manager or admin' : row.status === 'gateway_denied' ? 'not reached' : 'not required'}</li>
              </ul>
              {row.gateway_decision?.reasons?.length ? <p className="mt-1 text-xs text-muted">Reasons: {row.gateway_decision.reasons.join('; ')}</p> : null}
            </div>
            <Link to="/interventions" className="text-sm" onClick={onClose}>Open in Interventions →</Link>
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={onClose}>{row ? 'Close' : 'Cancel'}</Button>
          {!row && <Button variant="primary" disabled={!identity || propose.isPending || why.trim().length < 3} onClick={() => propose.mutate({ case_id: caseId, intervention_type: t, rationale: why.trim(), risk })} data-testid="propose-submit">Propose</Button>}
        </div>
      </div>
    </div>
  )
}

/** Ask Northstar about what the user is looking at. The question, the page context and the evidence all travel through the gateway. */
export function AskPanel({ context, suggestions, initial, autoRun }: { context: { page: string; case_id?: string; supplier_id?: string; control?: string }; suggestions: string[]; initial?: string; autoRun?: boolean }) {
  const ask = useAsk()
  const [q, setQ] = useState(initial ?? '')
  const [evidence, setEvidence] = useState<string[] | null>(null)
  const [propose, setPropose] = useState<NonNullable<AskResponse['actions_suggested']>[number] | null>(null)
  const ran = useRef(false)
  const run = (question: string) => { if (question.trim()) ask.mutate({ question: question.trim(), context }) }
  useEffect(() => { if (autoRun && initial && !ran.current) { ran.current = true; run(initial) } }, [autoRun, initial])
  const d = ask.data
  const err = ask.error as ApiError | null
  return (
    <div className="space-y-3">
      <form onSubmit={(e) => { e.preventDefault(); run(q) }} className="flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Question for Northstar" placeholder="Ask about what you are looking at…" maxLength={500} className="min-w-0 flex-1 rounded-md border border-line bg-panel2 px-3 py-2" data-testid="ask-input" />
        <Button variant="primary" type="submit" disabled={ask.isPending || !q.trim()} data-testid="ask-submit">{ask.isPending ? 'Asking…' : 'Ask'}</Button>
      </form>
      <div className="flex flex-wrap gap-1.5">
        {suggestions.map((s) => <button key={s} type="button" className="rounded-full border border-line px-2.5 py-0.5 text-xs hover:border-accent" onClick={() => { setQ(s); run(s) }}>{s}</button>)}
      </div>
      {ask.isPending && <p role="status" className="text-sm text-muted">Checking the question, retrieving evidence and verifying the answer…</p>}
      {ask.isError && (err?.status === 429 ? <Callout tone="warn" title="Model spend limit reached">{err.message}. Answers fall back to templates only after the cap resets.</Callout> : <ErrorState error={ask.error} />)}
      {d && (
        <div className="space-y-2" data-testid="ask-result">
          <GatewayChip g={d.gateway} />
          {d.blocked ? (
            <Callout tone="bad" title="This request was stopped before it reached the assistant">{d.gateway.reason ?? 'The security checks flagged it.'} Rephrase the question and try again.</Callout>
          ) : (
            <>
              {d.abstained && <Callout tone="warn" title="Northstar abstained">{d.abstain_reason}</Callout>}
              <div className="rounded-md border border-line bg-panel2 p-3 text-sm" data-testid="ask-answer">{d.answer}</div>
              <ClaimList claims={d.claims ?? []} onEvidence={(ids) => setEvidence(ids)} />
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                <Badge tone={d.model === 'template-fallback' ? 'warn' : 'neutral'}>{d.model === 'template-fallback' ? 'template answer (no model)' : d.model}</Badge>
                {typeof d.supported_claims === 'number' && <span>{d.supported_claims}/{d.total_claims} claims verified</span>}
                {d.cost_usd !== undefined && <span>cost ${fmt(d.cost_usd, 4)}</span>}
                {d.latency_ms !== undefined && <span>{fmt(d.latency_ms, 0)} ms</span>}
                {d.evidence && <button type="button" className="underline" onClick={() => setEvidence([])}>all evidence ({d.evidence.length})</button>}
                {d.trace_id && <Link to="/observability" search={{ trace: d.trace_id }} className="underline">trace {d.trace_id.slice(0, 8)}</Link>}
              </div>
              {d.notes?.filter(Boolean).length ? <p className="text-xs text-muted">{d.notes.join(' · ')}</p> : null}
              {d.actions_suggested?.map((a, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2 rounded-md border border-line p-2 text-sm">
                  <Badge tone="info">suggested action</Badge><span>{TYPE_LABEL[a.intervention_type] ?? a.intervention_type} on case {a.case_id}</span>
                  <Button onClick={() => setPropose(a)} data-testid="suggested-propose">Propose</Button>
                </div>
              ))}
            </>
          )}
        </div>
      )}
      {evidence && d && <EvidenceDrawer items={d.evidence ?? []} highlight={evidence} onClose={() => setEvidence(null)} />}
      {propose && <ProposeDialog caseId={propose.case_id} risk={0.5} rationale={propose.rationale} type={propose.intervention_type} onClose={() => setPropose(null)} />}
    </div>
  )
}

export function MiniStat({ label, children }: { label: string; children: ReactNode }) {
  return <div><div className="text-xs text-muted">{label}</div><div className="text-sm font-medium">{children}</div></div>
}

export function SourceNote({ children }: { children: ReactNode }) {
  return <p className="mt-2 text-xs text-muted">{children}</p>
}

export function CardLink({ to, children }: { to: string; children: ReactNode }) {
  return <Link to={to} className="text-sm">{children} →</Link>
}

export { Card }
