import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useDataQuality, useEvidence, useExperiments, useLimits, useLineage, useModels, useServices, useTrace } from '../api/hooks'
import { Async, Badge, Bar, Button, Callout, Card, DataTable, Grid, PageHeader } from '../components/ui'
import { StatusBadge } from '../components/domain'
import { ProvBadge } from '../components/Prov'
import { P1_BASE, recentCalls } from '../api/client'
import { fmt } from '../lib/format'
import { useAlerts } from '../components/Shell'

function FreeTierLimits() {
  const q = useLimits()
  const state = (s: string) => ({ ok: ['good', 'available'], cooling: ['warn', 'cooling down'], no_key: ['neutral', 'no key'], unavailable: ['bad', 'not available to this key'] } as Record<string, [any, string]>)[s] ?? ['neutral', s]
  return (
    <Card className="mt-3" title="Free-tier AI models (Gemini + Groq)" subtitle="Two keys only. Provider 429s set the cooldowns; the caps are the ones configured in config/free_models.yaml." actions={q.data && <Badge tone={q.data.assistant.summary.startsWith('all') ? 'good' : q.data.assistant.summary.startsWith('no free') ? 'bad' : 'warn'}>{q.data.assistant.summary}</Badge>}>
      <Async q={q} rows={3}>{(l) => (
        <div className="space-y-3" data-testid="limits-card">
          <DataTable caption="Free-tier models" rows={l.assistant.models} rowKey={(m) => m.provider + m.model} columns={[
            { key: 'p', header: 'Provider', render: (m) => m.provider }, { key: 'm', header: 'Model', render: (m) => <span className="font-mono text-xs">{m.model}</span> },
            { key: 's', header: 'State', render: (m) => { const [t, label] = state(m.state); return <Badge tone={t}>{label}{m.state === 'cooling' && m.cooldown_scope ? ` · ${m.cooldown_scope}` : ''}</Badge> } },
            { key: 'r', header: 'Back in', align: 'right', render: (m) => m.retry_after_s != null ? (m.retry_after_s < 90 ? `${Math.round(m.retry_after_s)} s` : m.retry_after_s < 5400 ? `${Math.round(m.retry_after_s / 60)} min` : `${(m.retry_after_s / 3600).toFixed(1)} h`) : '—' },
            { key: 'u', header: 'Used min / today', align: 'right', render: (m) => m.used_day == null ? '—' : `${m.used_minute} / ${m.used_day}` },
            { key: 'c', header: 'Your caps (rpm / rpd / tpm)', render: (m) => m.caps ? [m.caps.rpm, m.caps.rpd, m.caps.tpm].map((x) => x ?? '—').join(' / ') : '—' },
          ]} />
          <p className="text-sm">Gateway allowance: <strong>{l.gateway.used_by_you ?? '—'}</strong> of {l.gateway.per_user_daily} questions used by you today; {l.gateway.used_overall} of {l.gateway.global_daily} overall; resets in {Math.round(l.gateway.resets_in_s / 3600)} h (00:00 UTC).</p>
          <ul className="list-disc pl-5 text-xs text-muted">{l.assistant.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>)}</Async>
    </Card>
  )
}

export function Observability() {
  const search = (useSearch({ strict: false }) as { trace?: string })
  const nav = useNavigate()
  const [id, setId] = useState(search.trace ?? '')
  const trace = useTrace(search.trace ?? null)
  const services = useServices()
  const p1 = useQuery({ queryKey: ['p1-health'], refetchInterval: 20_000, retry: false, queryFn: async () => { const r = await fetch(`${P1_BASE}/health`); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<{ status: string }> } })
  const [calls, setCalls] = useState(recentCalls())
  useEffect(() => { const t = setInterval(() => setCalls(recentCalls()), 3000); return () => clearInterval(t) }, [])
  const spans = trace.data?.assistant?.spans ?? []
  const total = Math.max(1, trace.data?.assistant?.total_ms ?? 1)
  return (
    <div>
      <PageHeader title="Observability" subtitle="One trace id follows a request from the console through the gateway and the assistant to P1 and the model." />
      <Grid cols={3}>
        <Card title="Analytics (P1)">{p1.isError ? <Badge tone="bad">unreachable</Badge> : p1.data ? <Badge tone="good">{p1.data.status}</Badge> : <Badge>checking…</Badge>}</Card>
        <Card title="Gateway (P3)">{services.isError ? <Badge tone="bad">unreachable</Badge> : services.data ? <Badge tone="good">{services.data.gateway.status}</Badge> : <Badge>checking…</Badge>}</Card>
        <Card title="Assistant (P2) via gateway">{services.data ? (services.data.assistant.reachable ? <Badge tone="good">reachable</Badge> : <Badge tone="warn">{services.data.assistant.configured ? 'unreachable' : 'not configured'}</Badge>) : <Badge>checking…</Badge>}<p className="mt-1 text-xs text-muted">{services.data?.assistant.detail}</p></Card>
      </Grid>
      <FreeTierLimits />
      <Card className="mt-3" title="Trace">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); nav({ to: '/observability', search: { trace: id.trim() || undefined } }) }}>
          <input value={id} onChange={(e) => setId(e.target.value)} aria-label="Trace id" placeholder="32-character trace id" className="min-w-0 flex-1 rounded-md border border-line bg-panel2 px-3 py-1.5 font-mono text-sm" data-testid="trace-input" />
          <Button type="submit">Open</Button>
        </form>
        {search.trace && <div className="mt-3"><Async q={trace} rows={4}>{(t) => (
          <div className="space-y-3" data-testid="trace-view">
            <div className="text-sm">Trace <code>{t.trace_id}</code>{t.assistant && <> · {fmt(t.assistant.total_ms, 0)} ms · cost ${fmt(t.assistant.cost_usd, 4)}</>}</div>
            <div><h3 className="mb-1 text-sm font-semibold">Gateway</h3>
              <ul className="text-sm">{t.gateway.decisions.map((d, i) => <li key={i}><StatusBadge status={d.decision} /> {d.phase} · {d.layer ?? 'no layer'}{d.rule_id ? ` · ${d.rule_id}` : ''} <span className="text-xs text-muted">{fmt(d.latency_ms, 1)} ms</span></li>)}
                {t.gateway.actions.map((a, i) => <li key={`a${i}`}><StatusBadge status={a.effect} /> {a.tool}{a.stage ? ` · ${a.stage}` : ''}</li>)}
                {t.gateway.decisions.length + t.gateway.actions.length === 0 && <li className="text-muted">No gateway records for this trace.</li>}</ul></div>
            <div><h3 className="mb-1 text-sm font-semibold">Assistant spans</h3>
              {spans.length ? <ul className="space-y-1.5">{spans.map((s, i) => (
                <li key={i} className="text-sm"><div className="flex justify-between"><span><Badge>{s.kind}</Badge> {s.name}{s.attrs.cost_usd ? ` · $${fmt(Number(s.attrs.cost_usd), 4)}` : ''}{s.attrs.model ? ` · ${String(s.attrs.model)}` : ''}</span><span className="tabular-nums text-xs text-muted">{fmt(s.duration_ms, 1)} ms</span></div>
                  <div className="relative h-2 rounded bg-panel2"><div className="absolute h-2 rounded bg-accent" style={{ left: `${Math.min(98, (100 * s.start_ms) / total)}%`, width: `${Math.max(1.5, (100 * s.duration_ms) / total)}%` }} /></div></li>))}</ul> : <p className="text-sm text-muted">No assistant spans (the request did not reach it, or the trace has expired — they are kept 14 days).</p>}</div>
          </div>)}</Async></div>}
      </Card>
      <Card className="mt-3" title="This session's API calls" subtitle="Each carries a W3C traceparent; click a trace to open it.">
        {calls.length ? <DataTable caption="Recent calls" maxHeight="300px" rows={[...calls].reverse().slice(0, 40)} rowKey={(r) => `${r.at}${r.path}`} onRowClick={(r) => r.traceId && nav({ to: '/observability', search: { trace: r.traceId } })} columns={[
          { key: 't', header: 'Time', render: (r) => new Date(r.at).toLocaleTimeString() }, { key: 's', header: 'Service', render: (r) => r.service.toUpperCase() }, { key: 'm', header: 'Call', render: (r) => `${r.method} ${r.path}` },
          { key: 'st', header: 'Status', render: (r) => r.status }, { key: 'ms', header: 'ms', align: 'right', render: (r) => r.ms }, { key: 'tr', header: 'Trace', render: (r) => r.traceId.slice(0, 8) }]} /> : <p className="text-sm text-muted">No calls yet.</p>}
      </Card>
    </div>
  )
}

export function Models() {
  const q = useModels()
  return (
    <div>
      <PageHeader title="Model health" subtitle="Two models: the early-warning GRU that scores open cases, and the late-stage model that scores finished cases." />
      <Async q={q} rows={5}>{(m) => (
        <Grid cols={1}>
          <Card title="Early-warning model (scores open cases)" actions={<ProvBadge p="experimental" source={m.early_warning.name} />} subtitle={m.early_warning.target}>
            <DataTable caption="Early-warning held-out quality by events seen" rows={Object.entries(m.early_warning.per_k)} rowKey={([k]) => k} columns={[
              { key: 'k', header: 'Events seen (k)', render: ([k]) => k }, { key: 'a', header: 'ROC-AUC', align: 'right', render: ([, v]) => fmt(v.test_roc_auc, 3) },
              { key: 'c', header: '95% CI', align: 'right', render: ([, v]) => `${fmt(v.ci95[0], 3)}–${fmt(v.ci95[1], 3)}` }, { key: 'b', header: 'Base rate', align: 'right', render: ([, v]) => fmt(v.base_rate, 3) }, { key: 'n', header: 'n test', align: 'right', render: ([, v]) => fmt(v.n_test) }]} />
            <p className="mt-2 text-xs text-muted">{m.early_warning.note}</p>
          </Card>
          <Grid cols={2}>
            <Card title="Late-stage model (scores finished cases)" subtitle={`${m.late_stage_model.name}, trained ${m.late_stage_model.trained_at?.slice(0, 10)}, ${m.late_stage_model.calibration} calibration`}>
              <Callout tone="warn" title="Headline AUC is inflated">{fmt(m.late_stage_model.served.roc_auc, 3)} is on the configured SLA target, where ~95% of cases breach. On realistic per-category p75 targets it is lower; see ROI & uplift for the model-versus-rules comparison.</Callout>
              <p className="mt-2 text-xs text-muted">scikit-learn {m.late_stage_model.sklearn_version} · feature-code hash <code>{m.late_stage_model.feature_code_sha256}</code></p>
            </Card>
            <Card title="Governance checks">{m.governance_checks.map((c) => <div key={c.check} className="flex items-center gap-2 py-0.5 text-sm"><Badge tone="good">✓</Badge>{c.check}</div>)}</Card>
          </Grid>
        </Grid>
      )}</Async>
    </div>
  )
}

export function DataQuality() {
  const q = useDataQuality()
  return (
    <div>
      <PageHeader title="Data quality" subtitle="Checks over the event log. This is a historical dataset, so freshness is not alarmed." />
      <Async q={q} rows={5}>{(d) => (
        <Grid cols={1}>
          <Grid cols={3}><Card><div className="text-xs text-muted">Cases</div><div className="text-2xl font-semibold">{fmt(d.cases)}</div></Card><Card><div className="text-xs text-muted">Events</div><div className="text-2xl font-semibold">{fmt(d.events)}</div></Card><Card><div className="text-xs text-muted">Suppliers</div><div className="text-2xl font-semibold">{fmt(d.suppliers)}</div></Card></Grid>
          <Card title="Checks" subtitle={`${d.mode} · ${d.freshness_note}`}>
            <DataTable caption="Data quality checks" rows={d.checks} rowKey={(r) => r.check} columns={[{ key: 'c', header: 'Check', render: (r) => r.check }, { key: 's', header: 'Status', render: (r) => <StatusBadge status={r.status} /> }, { key: 'd', header: 'Detail', render: (r) => r.detail ?? '' }]} />
          </Card>
          <Card title="SLA coverage"><div className="space-y-2"><div className="flex justify-between text-sm"><span>Cases with an explicit category SLA</span><strong>{fmt(d.explicit_sla_share_pct, 0)}%</strong></div><Bar value={d.explicit_sla_share_pct} tone="good" /><p className="text-xs text-muted">The rest use a 240 h fallback, which is why configured-SLA breach rates sit near 95% and why model claims use realistic per-category targets.</p></div></Card>
        </Grid>
      )}</Async>
    </div>
  )
}

export function Lineage() {
  const q = useLineage()
  return (
    <div>
      <PageHeader title="Data lineage" subtitle="From the raw event log to the numbers on these pages." />
      <Async q={q} rows={4}>{(l) => (
        <Grid cols={1}>
          <Card title="Graph"><div className="flex flex-wrap items-center gap-2">{[0, 1, 2, 3, 4, 5].map((layer) => { const nodes = l.graph.nodes.filter((n) => n.layer === layer); return nodes.length ? <div key={layer} className="flex items-center gap-2"><div className="space-y-1">{nodes.map((n) => <div key={n.id} className="rounded-md border border-accent bg-panel2 px-3 py-1.5 text-sm">{n.label}</div>)}</div>{layer < 5 && <span aria-hidden className="text-muted">→</span>}</div> : null })}</div></Card>
          <Card title="Chains for headline metrics">{Object.entries(l.chains).map(([m, chain]) => <div key={m} className="mb-3"><h3 className="text-sm font-semibold">{m.replace(/_/g, ' ')}</h3><ol className="mt-1 flex flex-wrap items-center gap-1 text-sm">{(chain ?? []).map((c, i) => <li key={i} className="flex items-center gap-1"><Badge>{c}</Badge>{i < (chain?.length ?? 0) - 1 && <span aria-hidden>→</span>}</li>)}</ol></div>)}</Card>
        </Grid>
      )}</Async>
    </div>
  )
}

export function Architecture() {
  const parts = [
    ['P1 · operations-performance', 'Analytics and ML', 'Event-log cleaning, dbt marts, process mining, the replay clock, early-warning and late-stage models, AP controls, working capital, uplift simulation. Read-only /v1 API with a snapshot fallback.'],
    ['P2 · operations-assistant', 'Evidence-grounded copilot', 'Context-aware answers verified claim by claim, investigations, briefing, schema-validated natural-language filters, a persisted intervention ledger, traces and a spend guard.'],
    ['P3 · llm-security-gateway', 'AI security and governance', 'Pre/post-flight text checks, the action firewall (policy → taint → approval), persisted governance data, the attack lab. The only path from this console to AI and to actions.'],
    ['P4 · northstar-infra', 'Infrastructure', 'CI and infrastructure code for deploying the services.'],
  ]
  return (
    <div>
      <PageHeader title="Architecture" subtitle="Detect → investigate → explain → recommend → authorize → approve → audit." />
      <Grid cols={2}>{parts.map(([t, k, d]) => <Card key={t} title={t}><Badge tone="info">{k}</Badge><p className="mt-2 text-sm">{d}</p></Card>)}</Grid>
      <Card className="mt-3" title="Request path">
        <pre className="overflow-auto text-xs leading-relaxed">{`console ── data (read-only) ─────────────────────────► P1 /v1  (replay clock, snapshot fallback)
   │
   └─ questions, investigations, proposals, approvals ─► P3 gateway /v1
                                                          │ pre-flight: PII → injection ensemble → limits
                                                          ▼
                                                        P2 /v1 ── retrieval ── P1 /v1 ── model
                                                          │ (claim-support gate, spend guard)
                                                          ▼ proposed action
                                                        P3 action firewall: policy → taint → human approval
                                                          ▼
                                                        intervention ledger → P1 ledger (treat/holdout) → outcome → ROI`}</pre>
        <p className="mt-2 text-xs text-muted">One W3C trace id spans every hop. Honest limit: in this build the services run separately; the gateway and the assistant are wired by configuration, and the end-to-end demo test exercises them together locally.</p>
      </Card>
    </div>
  )
}

const RESULT: Record<string, 'bad' | 'warn' | 'good'> = { failed: 'bad', leaked: 'bad', tied: 'warn', no_gain: 'warn', resolved: 'good', promising: 'good' }
export function Experiments() {
  const q = useExperiments()
  return (
    <div>
      <PageHeader title="Experiments" subtitle="Everything that was tried, including what failed, leaked or tied. Negative results are the point." />
      <Async q={q} rows={5}>{(d) => (
        <Grid cols={2}>{d.experiments.map((e) => (
          <Card key={e.id} title={e.name} actions={<Badge tone={RESULT[e.result] ?? 'neutral'}>{e.result.replace('_', ' ')}</Badge>}>
            <p className="text-sm"><span className="text-muted">Hypothesis:</span> {e.hypothesis}</p><p className="mt-1 text-sm"><span className="text-muted">Result:</span> {e.detail}</p><p className="mt-1 text-sm"><span className="text-muted">Decision:</span> {e.decision}</p>
            <p className="mt-1 text-xs text-muted"><code>{e.doc}</code></p>
          </Card>))}</Grid>
      )}</Async>
    </div>
  )
}

const CLAIM_TONE: Record<string, 'good' | 'warn' | 'bad'> = { established: 'good', found_and_fixed: 'good', not_established: 'warn', simulation_only: 'warn', inflated: 'warn', refuted: 'bad', not_supported: 'bad' }
export function EvidencePage() {
  const q = useEvidence()
  return (
    <div>
      <PageHeader title="Evidence & limitations" subtitle="Every headline claim with its status." />
      <Async q={q} rows={5}>{(e) => (
        <Grid cols={1}>
          <Card title="Claims"><DataTable caption="Claims and their status" rows={e.claims} rowKey={(r) => r.claim} columns={[{ key: 'c', header: 'Claim', render: (r) => r.claim }, { key: 's', header: 'Status', render: (r) => <Badge tone={CLAIM_TONE[r.status] ?? 'neutral'}>{r.status.replace(/_/g, ' ')}</Badge> }, { key: 'e', header: 'Evidence', render: (r) => <span className="text-muted">{r.evidence}</span> }]} /></Card>
          <Card title="Known limitations"><ul className="list-disc space-y-1 pl-5 text-sm">{e.limitations.map((l) => <li key={l}>{l}</li>)}</ul></Card>
        </Grid>
      )}</Async>
    </div>
  )
}

export function Alerts() {
  const alerts = useAlerts()
  return (
    <div>
      <PageHeader title="Alerts" subtitle="Derived from the services' own state; this replay has no live alert feed." />
      <Card>{alerts.length ? <ul className="divide-y divide-line">{alerts.map((a, i) => <li key={i} className="flex items-center gap-3 py-2 text-sm"><Badge tone={a.sev === 'critical' ? 'bad' : a.sev === 'warning' ? 'warn' : 'info'}>{a.sev}</Badge><span className="flex-1">{a.text}</span>{a.to && <Link to={a.to}>View →</Link>}</li>)}</ul> : <p className="text-sm text-muted">No alerts.</p>}</Card>
    </div>
  )
}

export function NotFound() {
  return <Card title="Page not found"><p className="text-sm">That page does not exist. <Link to="/">Back to the overview</Link>.</p></Card>
}
