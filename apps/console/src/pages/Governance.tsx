import { useMemo, useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useApprovals, useGovEvents, useGovSummary, useGwConfig, useInterventions, useLabRun, useLabScenarios, usePolicyMatrix, useRules } from '../api/hooks'
import { Async, Badge, Bar, Button, Callout, Card, DataTable, Grid, PageHeader, Tabs, ErrorState, Collapsible } from '../components/ui'
import { StatusBadge } from '../components/domain'
import { ProvBadge } from '../components/Prov'
import { SeriesChart } from '../components/LazyCharts'
import { fmt, pct } from '../lib/format'
import { useSession } from '../state/session'
import { ApiError } from '../api/client'
import type { LabRun, LabScenario } from '../api/types'
import { describeGatewayEvent, ledgerTrace } from '../lib/audit'

export function Security() {
  const [window, setWindow] = useState('1h')
  const s = useGovSummary(window)
  const rules = useRules()
  const cfg = useGwConfig()
  const events = useGovEvents(40)
  const owasp = useMemo(() => {
    const byRule = new Map((rules.data?.rules ?? []).map((r) => [r.rule_id, r.owasp]))
    const out: Record<string, number> = {}
    Object.entries(s.data?.blocks_by_rule ?? {}).forEach(([rule, n]) => { const o = byRule.get(rule) ?? 'unmapped'; out[o] = (out[o] ?? 0) + n })
    return out
  }, [rules.data, s.data])
  return (
    <div>
      <PageHeader title="AI security" subtitle="Every question and action passes the gateway. These numbers come from its persisted decision log, not from in-process counters." actions={<Tabs label="Window" value={window} onChange={setWindow} options={['5m', '1h', '24h', '7d'].map((w) => ({ value: w, label: w }))} />} />
      <Async q={s} rows={5}>{(d) => (
        <Grid cols={1}>
          <Grid cols={4}>
            <Card><div className="text-xs text-muted">Requests (counted once)</div><div className="text-2xl font-semibold" data-testid="gov-requests">{fmt(d.requests)}</div><ProvBadge p="measured" source="gateway decision log" /></Card>
            <Card><div className="text-xs text-muted">Blocked</div><div className="text-2xl font-semibold" data-testid="gov-blocked">{fmt(d.blocked_requests)}</div><div className="text-xs text-muted">block rate {d.block_rate == null ? '—' : pct(100 * d.block_rate, 1)}</div></Card>
            <Card><div className="text-xs text-muted">Latency p50 / p95</div><div className="text-2xl font-semibold">{fmt(d.latency_ms.p50, 1)} / {fmt(d.latency_ms.p95, 1)} ms</div></Card>
            <Card><div className="text-xs text-muted">Requests with PII</div><div className="text-2xl font-semibold">{fmt(d.pii_requests)}</div></Card>
          </Grid>
          {d.requests === 0 && <Callout tone="info" title="No traffic in this window">Ask a question, or run a scenario in the Attack Lab, then refresh. An empty window is shown as empty, not as 0% blocked.</Callout>}
          <Grid cols={2}>
            <Card title="Requests and blocks over time">{d.series.length ? <SeriesChart series={d.series} label={`Requests and blocked requests over the last ${window}`} /> : <p className="text-sm text-muted">No data in this window.</p>}</Card>
            <Card title="Action decisions" subtitle="Tool calls the firewall saw in this window">
              <ul className="space-y-1.5 text-sm">{Object.entries(d.actions).map(([k, v]) => <li key={k} className="flex justify-between"><span>{k.replace(/_/g, ' ')}</span><strong>{fmt(v)}</strong></li>)}</ul>
            </Card>
          </Grid>
          <Grid cols={3}>
            <Card title="Blocks by layer">{Object.keys(d.blocks_by_layer).length ? <ul className="space-y-2">{Object.entries(d.blocks_by_layer).map(([k, v]) => <li key={k}><div className="flex justify-between text-sm"><span>{k.replace(/_/g, ' ')}</span><strong>{v}</strong></div><Bar value={v} max={Math.max(...Object.values(d.blocks_by_layer))} tone="bad" /></li>)}</ul> : <p className="text-sm text-muted">No blocks.</p>}</Card>
            <Card title="Blocks by OWASP LLM category">{Object.keys(owasp).length ? <ul className="space-y-2">{Object.entries(owasp).map(([k, v]) => <li key={k}><div className="flex justify-between text-sm"><span>{k}</span><strong>{v}</strong></div><Bar value={v} max={Math.max(...Object.values(owasp))} tone="warn" /></li>)}</ul> : <p className="text-sm text-muted">No blocks.</p>}<p className="mt-2 text-xs text-muted">ATLAS ids are pointers and unverified.</p></Card>
            <Card title="Active configuration"><Async q={cfg} rows={3}>{(c) => (
              <ul className="space-y-1 text-sm">
                <li>Classifier backend: <strong>{String(c.detector_backend.classifier)}</strong></li><li>Embedding layer: <strong>{String(c.detector_backend.embedding)}</strong></li>
                <li>Classifier threshold: {fmt(c.thresholds.classifier, 2)}</li><li>Policy hash: <code>{c.policy.sha256}</code> (default {c.policy.default_effect})</li>
                <li>Model integrity: {c.model_integrity.mode}</li><li>Assistant: {c.assistant_configured ? 'configured' : 'not configured'} · demo identity {c.demo_mode ? 'on' : 'off'}</li>
              </ul>)}</Async></Card>
          </Grid>
          <Card title="Recent events" subtitle={events.data?.note ?? undefined}>
            <Async q={events} rows={3} isEmpty={(e) => e.events.length === 0} empty={<p className="text-sm text-muted">No events yet.</p>}>{(e) => (
              <DataTable caption="Recent gateway events" maxHeight="320px" rows={e.events} rowKey={(r) => `${r.ts}${r.request_id ?? r.approval_id ?? r.tool}`} columns={[
                { key: 't', header: 'Time', render: (r) => new Date(r.ts * 1000).toLocaleTimeString() }, { key: 'k', header: 'Kind', render: (r) => r.kind },
                { key: 'd', header: 'Decision', render: (r) => <StatusBadge status={r.decision ?? r.effect ?? '—'} /> }, { key: 'l', header: 'Layer / stage', render: (r) => r.layer ?? r.stage ?? r.phase ?? '—' },
                { key: 'r', header: 'Rule / tool', render: (r) => r.rule_id ?? r.tool ?? r.rule ?? '—' }, { key: 'tr', header: 'Trace', render: (r) => r.trace_id ? <Link to="/observability" search={{ trace: r.trace_id }}>{r.trace_id.slice(0, 8)}</Link> : '—' }]} />
            )}</Async>
          </Card>
        </Grid>
      )}</Async>
    </div>
  )
}

const CELL: Record<string, { tone: 'good' | 'warn' | 'bad'; label: string }> = { allow: { tone: 'good', label: 'allow' }, approval: { tone: 'warn', label: 'approval' }, deny: { tone: 'bad', label: 'deny' } }
export function Permissions() {
  const q = usePolicyMatrix()
  return (
    <div>
      <PageHeader title="Tool permissions" subtitle="Role × tool, generated from the firewall's policy file. Default is deny; a call needs a matching rule and every argument must satisfy its constraints." />
      <Async q={q} rows={6}>{(m) => (
        <Grid cols={1}>
          <Card title={`${m.tools.length} tools`} subtitle={`Policy hash ${m.policy_sha256}`}>
            <div className="overflow-auto"><table><caption className="sr-only">Role by tool permission matrix</caption>
              <thead><tr><th scope="col">Tool</th><th scope="col">Kind</th>{m.roles.map((r) => <th key={r} scope="col">{r}</th>)}</tr></thead>
              <tbody>{m.tools.map((t) => <tr key={t.tool}><th scope="row" className="font-mono text-xs normal-case">{t.tool}</th><td>{t.kind}</td>{m.roles.map((r) => { const c = t.cells[r]; return <td key={r}><Badge tone={CELL[c.effect].tone} title={c.rules.map((x) => `${x.name}${x.require_role_separation ? ' (needs a different role to approve)' : ''}`).join('; ')}>{CELL[c.effect].label}</Badge></td> })}</tr>)}</tbody></table></div>
            <p className="mt-2 text-xs text-muted">“approval” means the call is queued for a human decision; payment release also needs an approver with a different role from the requester.</p>
          </Card>
          <Card title="Write-tool rules and argument constraints">
            {m.tools.filter((t) => t.kind === 'write').map((t) => (
              <div key={t.tool} className="mb-3"><h3 className="font-mono text-sm">{t.tool}</h3><p className="text-xs text-muted">taint: {Object.entries(t.taint).map(([k, v]) => `${k} → ${v}`).join(', ') || 'none'} · max per session {t.max_per_session ?? '—'}</p>
                {Object.values(t.cells).flatMap((c) => c.rules).filter((r, i, a) => a.findIndex((x) => x.name === r.name) === i).map((r) => <Collapsible key={r.name} summary={`${r.name}${r.require_role_separation ? ' · role separation' : ''}`}><ul className="text-xs text-muted">{Object.entries(r.args).map(([a, c]) => <li key={a}><code>{a}</code>: {c}</li>)}</ul></Collapsible>)}</div>
            ))}
          </Card>
        </Grid>
      )}</Async>
    </div>
  )
}

export function Approvals() {
  const { identity } = useSession()
  const q = useApprovals()
  const ints = useInterventions()
  return (
    <div>
      <PageHeader title="Approvals" subtitle="The gateway's human-approval queue. The requester can never decide their own request; payment release needs a different role." />
      {!identity && <Callout tone="info" title="Sign in as a manager or admin">The queue is visible to approvers only.</Callout>}
      {identity && !['manager', 'admin', 'finance'].includes(identity.role) && <Callout tone="warn" title="Not an approver role">Role “{identity.role}” cannot read the approval queue.</Callout>}
      {identity && ['manager', 'admin', 'finance'].includes(identity.role) && (
        <Card title="Queue" subtitle="Decide proposals from the Interventions page so the ledger and P1 stay in step.">
          <Async q={q} rows={4} isEmpty={(d) => d.approvals.length === 0} empty={<p className="text-sm text-muted">Nothing is waiting.</p>}>{(d) => (
            <DataTable caption="Approvals" rows={d.approvals} rowKey={(r) => r.id} columns={[
              { key: 'id', header: 'Id', render: (r) => <span className="font-mono text-xs">{r.id}</span> }, { key: 's', header: 'Status', render: (r) => <StatusBadge status={r.expired ? 'expired' : r.status} /> },
              { key: 't', header: 'Action', render: (r) => `${String(r.args.action ?? r.tool)} → ${String(r.args.target ?? '')}` }, { key: 'who', header: 'Requested by', render: (r) => `${r.requester_id} (${r.requester_role})` },
              { key: 'r', header: 'Risk', render: (r) => r.risk }, { key: 'sep', header: 'Role separation', render: (r) => r.require_role_separation ? <Badge tone="warn">different role required</Badge> : '—' },
              { key: 'e', header: 'Evidence', render: (r) => r.reasons.join('; ') || '—' }]} />
          )}</Async>
        </Card>
      )}
      <Card className="mt-3" title="Linked interventions"><Async q={ints} rows={2} isEmpty={(d) => d.interventions.length === 0} empty={<p className="text-sm text-muted">None.</p>}>{(d) => <ul className="text-sm">{d.interventions.slice(0, 8).map((i) => <li key={i.id}><Link to="/interventions">#{i.id}</Link> {i.case_id} <StatusBadge status={i.status} /></li>)}</ul>}</Async></Card>
    </div>
  )
}

function Timeline({ run }: { run: LabRun }) {
  const tone = (d: string) => (['block', 'deny', 'denied', 'refused'].includes(d) ? 'good' : ['require_approval', 'forwarded', 'skipped'].includes(d) ? 'warn' : ['allow', 'executed', 'reached', 'approved', 'returned'].includes(d) ? (run.defenses === 'off' ? 'bad' : 'neutral') : 'neutral')
  return (
    <ol className="space-y-2" aria-label="Step-by-step decision timeline" data-testid="lab-timeline">
      {run.timeline.map((s, i) => (
        <li key={i} className="rounded-md border border-line p-2 text-sm">
          <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-xs text-muted">{i + 1}</span><strong>{s.step.replace(/_/g, ' ')}</strong>{s.tool && <code className="text-xs">{s.tool}</code>}{s.layer && <Badge>{s.layer.replace(/_/g, ' ')}</Badge>}<Badge tone={tone(s.decision) as never}>{s.decision.replace(/_/g, ' ')}</Badge>{s.stage && <Badge tone="info">{s.stage}</Badge>}{s.harmful && <Badge tone="bad">harmful call</Badge>}</div>
          {s.reasons?.length ? <p className="mt-1 text-xs text-muted">{s.reasons.join('; ')}</p> : null}{s.detail && <p className="mt-1 text-xs text-muted">{s.detail}</p>}
        </li>
      ))}
    </ol>
  )
}

export function AttackLab() {
  const sc = useLabScenarios()
  const run = useLabRun()
  const { identity } = useSession()
  const [id, setId] = useState('TX-1')
  const [defenses, setDefenses] = useState<'on' | 'off'>('on')
  const [curated, setCurated] = useState(true)
  const scenarios: LabScenario[] = (sc.data?.scenarios ?? []).filter((s) => !curated || s.curated)
  const err = run.error as ApiError | null
  return (
    <div>
      <PageHeader title="Attack Lab" subtitle="Run a scenario through the live pipeline and watch each layer decide. “Defenses off” runs the undefended stub on a simulated upstream only; nothing real is ever touched." />
      <Callout tone="info" title="What this does and does not show">Action scenarios use a <strong>scripted</strong> agent that obeys the injection (the worst case). They measure the firewall, not how often real models are hijacked. ROT13 is a known, reported weakness.</Callout>
      <Grid cols={2} className="mt-3">
        <Card title="Scenario">
          <Async q={sc} rows={3}>{() => (
            <div className="space-y-3">
              <label className="block text-xs text-muted" htmlFor="scn">Scenario</label>
              <select id="scn" value={id} onChange={(e) => setId(e.target.value)} className="w-full rounded-md border border-line bg-panel2 px-2 py-1.5 text-sm" data-testid="lab-select">
                {scenarios.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.category.replace(/_/g, ' ')} · {s.title}</option>)}
              </select>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={curated} onChange={(e) => setCurated(e.target.checked)} /> curated scenarios only ({sc.data?.scenarios.filter((s) => s.curated).length}); untick for all {sc.data?.scenarios.length}</label>
              <Tabs label="Defenses" value={defenses} onChange={setDefenses} options={[{ value: 'on', label: 'Defenses on' }, { value: 'off', label: 'Defenses off (stub)' }]} />
              <div><Button variant="primary" onClick={() => run.mutate({ scenario_id: id, defenses, target: 'stub' })} disabled={!identity || run.isPending} data-testid="lab-run">{run.isPending ? 'Running…' : 'Run scenario'}</Button>{!identity && <span className="ml-2 text-xs text-muted">Sign in (demo) first.</span>}</div>
            </div>)}</Async>
        </Card>
        <Card title="Result" actions={run.data && <ProvBadge p="measured" source="live pipeline, scripted attacker" />}>
          {run.isError && (err?.status === 429 ? <Callout tone="warn" title="Rate limited">{err.message}</Callout> : <ErrorState error={run.error} />)}
          {!run.data && !run.isError && <p className="text-sm text-muted">Pick a scenario and run it.</p>}
          {run.data && (
            <div className="space-y-2" data-testid="lab-result">
              <p className="text-sm font-medium">{run.data.title}</p>
              <div className="flex flex-wrap items-center gap-2"><Badge tone={run.data.compromised ? 'bad' : 'good'}>{run.data.compromised ? 'COMPROMISED' : 'not compromised'}</Badge><span className="text-sm" data-testid="lab-outcome">{run.data.outcome}</span><span className="text-xs text-muted">{fmt(run.data.latency_ms, 1)} ms</span></div>
              <p className="text-xs text-muted">Tools executed: {run.data.tools_executed.length ? run.data.tools_executed.join(', ') : 'none'}</p>
              <Timeline run={run.data} />
            </div>)}
        </Card>
      </Grid>
      <Card className="mt-3" title="Benchmark results" subtitle="Committed evaluation tables stay here next to the live lab."><p className="text-sm">Action firewall: 86 scenarios (72 harmful, 14 benign) in <code>reports/p3_action_firewall.json</code>. See <Link to="/security">AI Security</Link> for live numbers and <Link to="/permissions">Tool Permissions</Link> for the policy behind them.</p></Card>
    </div>
  )
}

export function AuditLog() {
  const nav = useNavigate()
  const events = useGovEvents(200)
  const ints = useInterventions()
  const [trace, setTrace] = useState('')
  const merged = useMemo(() => {
    const g = (events.data?.events ?? []).map((e) => { const d = describeGatewayEvent(e); return { ts: e.ts, source: 'gateway' as const, what: d.what, who: e.session, trace: e.trace_id ?? '', detail: d.detail || e.tool || '' } })
    const p = (ints.data?.interventions ?? []).flatMap((i) => i.history.map((h) => ({ ts: h.ts, source: 'assistant ledger' as const, what: `intervention #${i.id} → ${h.status.replace(/_/g, ' ')}`, who: h.actor, trace: ledgerTrace(h.detail), detail: i.case_id })))
    return [...g, ...p].sort((a, b) => b.ts - a.ts).filter((r) => !trace || r.trace.startsWith(trace))
  }, [events.data, ints.data, trace])
  return (
    <div>
      <PageHeader title="Audit log" subtitle="Server-side records merged: the gateway's decisions (including who approved a held action) and the assistant's intervention history. Filter by trace id to follow one request end to end." />
      <Card className="mb-3"><label className="text-xs text-muted">Trace id (prefix)<input value={trace} onChange={(e) => setTrace(e.target.value.trim())} placeholder="4bf92f35…" className="mt-0.5 block w-72 rounded-md border border-line bg-panel2 px-2 py-1 text-sm text-ink" data-testid="audit-trace" /></label>{events.data?.note && <p className="mt-1 text-xs text-muted">{events.data.note}</p>}</Card>
      <Card><Async q={events} rows={5} isEmpty={() => merged.length === 0} empty={<p className="text-sm text-muted">No records{trace ? ' for this trace' : ''}.</p>}>{() => (
        <DataTable caption="Merged audit records" maxHeight="600px" rows={merged} rowKey={(r) => `${r.ts}${r.source}${r.what}${r.who}`} onRowClick={(r) => r.trace && nav({ to: '/observability', search: { trace: r.trace } })} columns={[
          { key: 't', header: 'Time', render: (r) => new Date(r.ts * 1000).toLocaleString() }, { key: 's', header: 'Source', render: (r) => <Badge>{r.source}</Badge> },
          { key: 'w', header: 'Record', render: (r) => r.what }, { key: 'a', header: 'Actor / session', render: (r) => r.who }, { key: 'd', header: 'Detail', render: (r) => r.detail }, { key: 'tr', header: 'Trace', render: (r) => r.trace ? r.trace.slice(0, 8) : '—' }]} />
      )}</Async></Card>
    </div>
  )
}
