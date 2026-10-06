import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useCase, useFlow, useNLFilter, useQueue, useRiskMap, useSupplier, useSuppliers, useVariants, type QueueFilters } from '../api/hooks'
import { Async, Badge, Bar, Button, Callout, Card, DataTable, Grid, PageHeader, Tabs, Collapsible } from '../components/ui'
import { AskPanel, GatewayChip, LimitsCallout, MiniStat, ProposeDialog, TierBadge, Timeline } from '../components/domain'
import { MetricValue, ProvBadge } from '../components/Prov'
import { FLOW_METRIC_LABEL, ProcessGraph, RiskValueScatter, SupplierQuadrant, type FlowMetric } from '../components/LazyCharts'
import { dateLabel, eur, fmt, hours, pct } from '../lib/format'
import type { FlowEdge } from '../api/types'
import { useSession } from '../state/session'

const STAGES = [['order', 'Requisition & order'], ['approval', 'Approval (SRM)'], ['changes', 'Order changes'], ['receipt', 'Goods / service receipt'], ['invoicing', 'Invoicing'], ['block', 'Payment block'], ['payment', 'Clearing & payment']]

export function ActionCenter() {
  const nav = useNavigate()
  const [filters, setFilters] = useState<QueueFilters>({ limit: 100 })
  const [nlText, setNlText] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const nl = useNLFilter()
  const q = useQueue(filters)
  const applyNl = async () => {
    const r = await nl.mutateAsync(nlText)
    if (!r.blocked && !r.rejected && r.target === 'queue' && r.filter) setFilters({ limit: 100, ...(r.filter as QueueFilters) })
  }
  const investigate = () => {
    const ids = [...selected].slice(0, 6)
    nav({ to: '/investigations', search: { q: `Investigate why these open cases are high risk and what they have in common: ${ids.join(', ')}` } })
  }
  return (
    <div>
      <PageHeader title="Action Center" subtitle="Open cases ranked by simulated expected loss = P(breach) × order value × an assumed cost share. A small order can never outrank a large one at similar risk." />
      <Card className="mb-3" title="Filters">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs text-muted">Min value (EUR)<input type="number" min={0} value={filters.min_value ?? ''} onChange={(e) => setFilters({ ...filters, min_value: e.target.value ? Number(e.target.value) : undefined })} className="mt-0.5 block w-32 rounded-md border border-line bg-panel2 px-2 py-1 text-sm text-ink" /></label>
          <label className="text-xs text-muted">Stage
            <select value={filters.stage ?? ''} onChange={(e) => setFilters({ ...filters, stage: e.target.value || undefined })} className="mt-0.5 block rounded-md border border-line bg-panel2 px-2 py-1 text-sm text-ink">
              <option value="">All stages</option>{STAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select></label>
          <label className="text-xs text-muted">Supplier<input value={filters.supplier ?? ''} onChange={(e) => setFilters({ ...filters, supplier: e.target.value || undefined })} placeholder="vendorID_0108" className="mt-0.5 block w-40 rounded-md border border-line bg-panel2 px-2 py-1 text-sm text-ink" /></label>
          <Button onClick={() => setFilters({ limit: 100 })}>Clear</Button>
          <form className="ml-auto flex min-w-[16rem] flex-1 items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (nlText.trim()) applyNl().catch(() => undefined) }}>
            <label className="min-w-0 flex-1 text-xs text-muted">Describe a filter in words
              <input value={nlText} onChange={(e) => setNlText(e.target.value)} placeholder="orders above 50k in invoicing" aria-label="Natural-language filter" className="mt-0.5 block w-full rounded-md border border-line bg-panel2 px-2 py-1 text-sm text-ink" data-testid="nl-input" /></label>
            <Button type="submit" disabled={!nlText.trim() || nl.isPending}>Apply</Button>
          </form>
        </div>
        {nl.data && (
          <div className="mt-2 space-y-1.5">
            <GatewayChip g={nl.data.gateway} />
            <LimitsCallout limits={nl.data.limits} />
            {nl.data.blocked ? <Callout tone="warn" title="The gateway blocked this phrasing">Short telegraphic phrases are sometimes flagged by the injection classifier (a measured false-positive rate). Rephrase as a full sentence, e.g. “show orders above 50k in invoicing”.</Callout>
              : nl.data.rejected ? <Callout tone="warn" title="Not a supported filter">{nl.data.reason}</Callout>
              : <p className="text-sm" data-testid="nl-restatement">{nl.data.restatement} <Badge>{nl.data.source}</Badge></p>}
          </div>
        )}
        {nl.isError && <Callout tone="warn" title="Sign in to use the filter box">{(nl.error as Error).message}</Callout>}
      </Card>
      <Card title={q.data ? `${fmt(q.data.total_matching)} matching cases` : 'Cases'} subtitle={q.data?.driver_note} actions={<Button disabled={selected.size === 0} onClick={investigate}>Investigate {selected.size || ''} selected</Button>}>
        <Async q={q} rows={6} isEmpty={(d) => d.rows.length === 0} empty={<p className="text-sm text-muted">No open cases match at this clock.</p>}>{(d) => (
          <>
            <DataTable caption="Open cases ranked by expected loss" rows={d.rows} rowKey={(r) => r.case_id} selectable selected={selected} onSelect={setSelected} maxHeight="560px"
              onRowClick={(r) => nav({ to: '/cases/$caseId', params: { caseId: r.case_id } })}
              columns={[
                { key: 'c', header: 'Case', render: (r) => <span className="font-mono text-xs">{r.case_id}</span> },
                { key: 'sup', header: 'Supplier', render: (r) => r.supplier_id },
                { key: 't', header: 'Tier', render: (r) => <TierBadge tier={r.tier} /> },
                { key: 'st', header: 'Status', render: (r) => r.status === 'already_late' ? <Badge tone="bad">past target</Badge> : <Badge>{r.status}</Badge> },
                { key: 'p', header: 'P(breach)', align: 'right', render: (r) => <span title={r.p_breach_provenance}>{fmt(r.p_breach, 2)} <ProvBadge p={r.p_breach_provenance} /></span> },
                { key: 'v', header: 'Value', align: 'right', render: (r) => eur(r.value_eur) },
                { key: 'l', header: 'Exp. loss', align: 'right', render: (r) => eur(r.expected_loss_eur) },
                { key: 'stage', header: 'Stage', render: (r) => r.stage },
                { key: 'i', header: 'Idle', align: 'right', render: (r) => hours(r.idle_hours) },
              ]} />
            <p className="mt-2 text-xs text-muted">{d.ranking.note} Assumption: {pct(100 * Number(d.ranking.assumption?.value ?? 0), 0)} of order value per breach <ProvBadge p="simulated" source="config/expected_loss.yaml" />.</p>
          </>
        )}</Async>
      </Card>
    </div>
  )
}

export function Case360() {
  const { caseId } = (useParams({ strict: false }) as { caseId: string })
  const q = useCase(caseId)
  const { identity } = useSession()
  const [propose, setPropose] = useState(false)
  return (
    <div>
      <p className="mb-1 text-xs text-muted"><Link to="/queue">Action Center</Link> / {caseId}</p>
      <Async q={q} rows={6}>{(c) => (
        <>
          <PageHeader title={`Case ${c.case_id}`} subtitle={<>Supplier <Link to="/suppliers/$supplierId" params={{ supplierId: c.supplier_id }}>{c.supplier_id}</Link> · category {c.category} · as of {dateLabel(c.as_of)} · {c.events_seen} events seen</>}
            actions={c.risk ? <Button variant="primary" onClick={() => setPropose(true)} data-testid="propose-open">Propose intervention</Button> : undefined} />
          {c.status === 'closed' && c.outcome && <Callout tone="info" title="This case had already closed at the replay clock">It took {hours(c.outcome.cycle_hours)} against a target of {hours(c.outcome.target_hours)}: {c.outcome.breached_realistic_target ? 'breached' : 'within target'}.</Callout>}
          {c.risk && (
            <Grid cols={4}>
              <Card><MiniStat label="P(breach)"><MetricValue m={c.risk.p_breach} /></MiniStat></Card>
              <Card><MiniStat label="Simulated expected loss"><MetricValue m={c.risk.expected_loss_eur} fmtValue={(v) => eur(Number(v))} /></MiniStat></Card>
              <Card><MiniStat label="Tier"><TierBadge tier={c.risk.tier} /></MiniStat></Card>
              <Card><MiniStat label="Value so far">{eur(c.risk.value_eur)}</MiniStat></Card>
            </Grid>
          )}
          <Grid cols={2} className="mt-3">
            <Card title="Why is this ranked high?" subtitle={c.driver_note}>
              {c.drivers?.length ? (
                <ul className="space-y-2.5">
                  {c.drivers.map((d) => (
                    <li key={d.name}>
                      <div className="flex items-center justify-between text-sm"><span>{d.name} <ProvBadge p="descriptive" /></span><strong>{fmt(d.value, 1)} {d.unit}</strong></div>
                      <Bar value={Math.min(Math.abs(d.value ?? 0), 100)} max={Math.max(100, Math.abs(d.value ?? 0))} tone={d.raises_risk ? 'warn' : 'good'} />
                      {d.note && <p className="text-xs text-muted">{d.note}</p>}
                    </li>
                  ))}
                </ul>
              ) : <p className="text-sm text-muted">No drivers for a closed case.</p>}
              {c.risk?.model_quality && <p className="mt-2 text-xs text-muted">Early-warning model at k = {c.risk.model_quality.k}: held-out ROC-AUC {fmt(c.risk.model_quality.test_roc_auc, 2)} (95% CI {fmt(c.risk.model_quality.ci95[0], 2)}–{fmt(c.risk.model_quality.ci95[1], 2)}), n = {c.risk.model_quality.n_test}. The GRU has no SHAP, so these are descriptive facts, not attributions.</p>}
            </Card>
            <Card title="Process timeline" subtitle={c.longest_wait ? `Longest wait: ${hours(c.longest_wait.gap_hours)} before “${c.longest_wait.activity}”` : undefined}>
              <div className="max-h-96 overflow-auto"><Timeline events={c.timeline} /></div>
            </Card>
          </Grid>
          <Grid cols={2} className="mt-3">
            <Card title="AP exceptions & policy">
              {c.ap_exceptions?.length ? <ul className="text-sm">{c.ap_exceptions.map((x, i) => <li key={i}>{x.control_id} · {x.severity} · {eur(x.exposure_eur)}</li>)}</ul> : <p className="text-sm text-muted">No AP exceptions recorded for this case.</p>}
              <p className="mt-2 text-xs text-muted">{c.policy_note}</p>
            </Card>
            <Card title="Ask about this case" subtitle="Answers are verified claim by claim against live data and policy">
              <AskPanel context={{ page: 'case', case_id: c.case_id }} suggestions={['Why is this case at risk?', 'What should we do about it?', 'Can we hold payment?']} />
            </Card>
          </Grid>
          {!identity && <p className="mt-2 text-xs text-muted">Sign in (demo) to ask questions or propose an intervention.</p>}
          {propose && c.risk && <ProposeDialog caseId={c.case_id} risk={Number(c.risk.p_breach.value ?? 0.5)} rationale={`Case ${c.case_id} is ${c.risk.tier} on expected loss (${eur(Number(c.risk.expected_loss_eur.value))}).`} onClose={() => setPropose(false)} />}
        </>
      )}</Async>
    </div>
  )
}

export function Suppliers() {
  const nav = useNavigate()
  const [sort, setSort] = useState('ci_lower')
  const [minN, setMinN] = useState(20)
  const q = useSuppliers(sort, minN)
  return (
    <div>
      <PageHeader title="Suppliers" subtitle="Breach rates use only cases whose target window has elapsed at the replay clock, with Wilson 95% intervals; ranked by the interval's lower bound so a supplier with 3 cases cannot top the list." />
      <Card className="mb-3"><div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-muted">Rank by
          <select value={sort} onChange={(e) => setSort(e.target.value)} className="mt-0.5 block rounded-md border border-line bg-panel2 px-2 py-1 text-sm text-ink">
            <option value="ci_lower">Breach rate (CI lower bound)</option><option value="breach_rate">Breach rate (point)</option><option value="expected_loss">Open expected loss</option><option value="volume">Volume</option></select></label>
        <label className="text-xs text-muted">Minimum cases (n)<input type="number" min={1} value={minN} onChange={(e) => setMinN(Math.max(1, Number(e.target.value) || 1))} className="mt-0.5 block w-24 rounded-md border border-line bg-panel2 px-2 py-1 text-sm text-ink" /></label>
      </div></Card>
      <Async q={q} rows={6} isEmpty={(d) => d.rows.length === 0} empty={<Callout tone="warn" title="No supplier has enough cases at this clock">Lower the minimum n or move the replay clock later.</Callout>}>{(d) => (
        <Grid cols={1}>
          <Card title="Supplier quadrant" subtitle="Open expected loss × breach rate; bubble = closed cases; red = has open cases already past target">
            <SupplierQuadrant onOpen={(id) => nav({ to: '/suppliers/$supplierId', params: { supplierId: id } })} points={d.rows.map((r) => ({ id: r.supplier_id, x: r.open_expected_loss_eur + 1, y: Number(r.breach_rate.value), r: r.closed_cases, hot: r.open_already_late > 0, label: `${r.supplier_id}: ${fmt(Number(r.breach_rate.value), 1)}% breach (n = ${r.closed_cases}), ${r.open_cases} open` }))} />
          </Card>
          <Card title={`${d.count} suppliers`} subtitle={d.note}>
            <DataTable caption="Suppliers" rows={d.rows} rowKey={(r) => r.supplier_id} onRowClick={(r) => nav({ to: '/suppliers/$supplierId', params: { supplierId: r.supplier_id } })} maxHeight="520px"
              columns={[
                { key: 's', header: 'Supplier', render: (r) => r.supplier_id },
                { key: 'n', header: 'n', align: 'right', render: (r) => r.closed_cases },
                { key: 'b', header: 'Breach rate (95% CI)', align: 'right', render: (r) => <span>{fmt(Number(r.breach_rate.value), 1)}% <span className="text-xs text-muted">({fmt(r.breach_rate.ci95?.[0], 0)}–{fmt(r.breach_rate.ci95?.[1], 0)})</span> <ProvBadge p={r.breach_rate.provenance} source={r.breach_rate.source} n={r.breach_rate.n} /></span> },
                { key: 'p50', header: 'Cycle p50 / p90', align: 'right', render: (r) => `${fmt(r.p50_days, 0)} / ${fmt(r.p90_days, 0)} d` },
                { key: 'o', header: 'Open', align: 'right', render: (r) => `${r.open_cases} (${r.open_already_late} late)` },
                { key: 'l', header: 'Open exp. loss', align: 'right', render: (r) => eur(r.open_expected_loss_eur) },
              ]} />
          </Card>
        </Grid>
      )}</Async>
    </div>
  )
}

export function Supplier360() {
  const { supplierId } = (useParams({ strict: false }) as { supplierId: string })
  const nav = useNavigate()
  const q = useSupplier(supplierId)
  return (
    <div>
      <p className="mb-1 text-xs text-muted"><Link to="/suppliers">Suppliers</Link> / {supplierId}</p>
      <Async q={q} rows={5}>{(s) => (
        <>
          <PageHeader title={`Supplier ${s.supplier_id}`} subtitle={`As of ${dateLabel(s.as_of)} · ${s.open_count} open cases`} />
          <Grid cols={4}>
            <Card><MiniStat label="Cases past their target window"><MetricValue m={s.closed_cases} /></MiniStat></Card>
            <Card><MiniStat label="Breach rate (95% CI)"><MetricValue m={s.breach_rate} />{s.breach_rate.ci95 && <span className="text-xs text-muted"> {fmt(s.breach_rate.ci95[0], 0)}–{fmt(s.breach_rate.ci95[1], 0)}%</span>}</MiniStat></Card>
            <Card><MiniStat label="Cycle p50 / p90 (ended cases)">{fmt(s.cycle_days.p50, 0)} / {fmt(s.cycle_days.p90, 0)} d <span className="text-xs text-muted">peer p50 {fmt(s.cycle_days.peer_p50, 0)} d</span></MiniStat></Card>
            <Card><MiniStat label="Value (ended cases)"><MetricValue m={s.value_closed_eur} fmtValue={(v) => eur(Number(v))} /></MiniStat></Card>
          </Grid>
          <Grid cols={2} className="mt-3">
            <Card title="Open cases by expected loss">
              {s.open_cases.length ? <DataTable caption="Open cases" rows={s.open_cases} rowKey={(r) => r.case_id} onRowClick={(r) => nav({ to: '/cases/$caseId', params: { caseId: r.case_id } })} columns={[
                { key: 'c', header: 'Case', render: (r) => <span className="font-mono text-xs">{r.case_id}</span> }, { key: 't', header: 'Tier', render: (r) => <TierBadge tier={r.tier} /> },
                { key: 'v', header: 'Value', align: 'right', render: (r) => eur(r.value_eur) }, { key: 'l', header: 'Exp. loss', align: 'right', render: (r) => eur(r.expected_loss_eur) }]} /> : <p className="text-sm text-muted">No open cases.</p>}
            </Card>
            <Card title="Ask about this supplier"><AskPanel context={{ page: 'supplier', supplier_id: s.supplier_id }} suggestions={[`How is supplier ${s.supplier_id} performing?`, 'Should we escalate this supplier?']} /></Card>
          </Grid>
          <Card className="mt-3" title="Breach rate by target-window month">{s.trend.length ? <ul className="grid grid-cols-2 gap-x-6 text-sm sm:grid-cols-3">{s.trend.map((t) => <li key={t.month} className="flex justify-between"><span>{t.month}</span><span>{fmt(t.breach_rate, 0)}% <span className="text-xs text-muted">n = {t.n}</span></span></li>)}</ul> : <p className="text-sm text-muted">No month has enough cases.</p>}</Card>
          {s.ap_exceptions.length > 0 && <Card className="mt-3" title="AP exceptions"><ul className="text-sm">{s.ap_exceptions.map((x, i) => <li key={i}>{x.control_id} · {x.severity} · {eur(x.exposure_eur)}</li>)}</ul></Card>}
        </>
      )}</Async>
    </div>
  )
}

export function RiskMap() {
  const nav = useNavigate()
  const q = useRiskMap()
  return (
    <div>
      <PageHeader title="Risk map" subtitle="Which risky cases matter financially? Click a dot to open the case." />
      <Async q={q} rows={6}>{(r) => (
        <Grid cols={1}>
          <Card title={`${r.points.length} open cases by order value and breach probability`} actions={<ProvBadge p="experimental" source="early-warning GRU ensemble" />}>
            <RiskValueScatter height={420} points={r.points.map((p) => ({ id: p.case_id, risk: p.p_breach, value: p.value_eur, tier: p.tier, label: `${p.case_id} · ${p.stage}` }))} onOpen={(id) => nav({ to: '/cases/$caseId', params: { caseId: id } })} />
          </Card>
          <Card title="Risk portfolio" subtitle="Open cases by risk tier (rows) and value tercile (columns)">
            <table><thead><tr><th scope="col" /> {r.matrix.value_tiers.map((v) => <th key={v} scope="col" className="text-right">{v} value</th>)}</tr></thead>
              <tbody>{r.matrix.risk_tiers.map((rt, i) => <tr key={rt}><th scope="row" className="capitalize">{rt}</th>{r.matrix.counts[i].map((c, j) => <td key={j} className="text-right tabular-nums">{fmt(c)}</td>)}</tr>)}</tbody></table>
          </Card>
        </Grid>
      )}</Async>
    </div>
  )
}

const FLOW_ORDER = ['order', 'approval', 'changes', 'receipt', 'invoicing', 'block', 'payment']
export function ProcessMining() {
  const nav = useNavigate()
  const [group, setGroup] = useState<'stage' | 'activity'>('stage')
  const [metric, setMetric] = useState<FlowMetric>('median_hours')
  const [edge, setEdge] = useState<FlowEdge | null>(null)
  const flow = useFlow(group)
  const variants = useVariants()
  const label = useMemo(() => Object.fromEntries((flow.data?.nodes ?? []).map((n) => [n.id, n.label])), [flow.data])
  return (
    <div>
      <PageHeader title="Process mining" subtitle="Directly-follows graph over cases closed at the replay clock. Edge colour is relative to the chosen metric; thickness is frequency." />
      <div className="mb-3 flex flex-wrap gap-3">
        <Tabs label="Grouping" value={group} onChange={setGroup} options={[{ value: 'stage', label: 'Business stages' }, { value: 'activity', label: 'Raw activities' }]} />
        <Tabs label="Metric" value={metric} onChange={setMetric} options={(Object.keys(FLOW_METRIC_LABEL) as FlowMetric[]).map((m) => ({ value: m, label: FLOW_METRIC_LABEL[m] }))} />
      </div>
      <Async q={flow} rows={6}>{(f) => (
        <Grid cols={1}>
          <Card title={`${f.nodes.length} ${group === 'stage' ? 'stages' : 'activities'}, ${f.edges.length} transitions`} subtitle={f.note} actions={<ProvBadge p="measured" n={f.closed_cases} source="closed cases at the clock" />}>
            <ProcessGraph nodes={f.nodes} edges={f.edges} metric={metric} onEdge={setEdge} order={group === 'stage' ? FLOW_ORDER : undefined} />
            {f.unmapped_activities.length > 0 && <Callout tone="warn" title="Unmapped activities">{f.unmapped_activities.join(', ')}</Callout>}
          </Card>
          <Card title={edge ? `${label[edge.from] ?? edge.from} → ${label[edge.to] ?? edge.to}` : 'Transition details'} subtitle={edge ? undefined : 'Click an edge in the graph'}>
            {edge ? (
              <div>
                <Grid cols={4}>
                  <MiniStat label="Transitions">{fmt(edge.transitions)} ({fmt(edge.cases)} cases)</MiniStat>
                  <MiniStat label="Median / P75 / P90 wait">{hours(edge.median_hours)} / {hours(edge.p75_hours)} / {hours(edge.p90_hours)}</MiniStat>
                  <MiniStat label="Breach contribution">{pct(edge.breach_contribution_pct)}</MiniStat>
                  <MiniStat label="Cases breaching through this edge">{pct(edge.breach_rate_pct)}</MiniStat>
                </Grid>
                <div className="mt-2 flex gap-2"><Button onClick={() => nav({ to: '/ask', search: { q: `Why is the transition from "${label[edge.from] ?? edge.from}" to "${label[edge.to] ?? edge.to}" slow, and what should we do?` } })}>Ask about this transition</Button></div>
              </div>
            ) : <p className="text-sm text-muted">Edges carry median, P75 and P90 wait and their share of breaching cases' waiting time.</p>}
          </Card>
          <Card title="Variants"><Async q={variants} rows={3}>{(v) => (
            <DataTable caption="Process variants" rows={v.variants} rowKey={(r) => r.variant} columns={[
              { key: 'v', header: 'Variant', render: (r) => <span className="font-mono text-xs break-all">{r.variant}</span> }, { key: 'c', header: 'Cases', align: 'right', render: (r) => fmt(r.cases) },
              { key: 's', header: 'Share', align: 'right', render: (r) => pct(r.share_pct) }, { key: 'b', header: 'Breach', align: 'right', render: (r) => pct(r.breach_rate_pct) }, { key: 'm', header: 'Median', align: 'right', render: (r) => `${fmt(r.median_days, 0)} d` }]} />
          )}</Async></Card>
          <Collapsible summary="Why only closed cases?"><p className="text-sm text-muted">Process statistics are computed from cases that had already closed at the clock, so no event after it is used. Move the clock later to include more of the log.</p></Collapsible>
        </Grid>
      )}</Async>
    </div>
  )
}
