import { useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useBrief, useBriefingFacts, useDataQuality, useFinanceControls, useFlow, useInterventions, useModels, useOverview, useQueue, useRiskMap } from '../api/hooks'
import { Async, Badge, Button, Callout, Card, DataTable, Grid, PageHeader, Bar, ErrorState } from '../components/ui'
import { Kpi, TierBadge, EvidenceDrawer } from '../components/domain'
import { MetricValue, ProvBadge } from '../components/Prov'
import { RiskValueScatter, TrendChart } from '../components/LazyCharts'
import { dateLabel, eur, fmt } from '../lib/format'
import { useSession } from '../state/session'
import type { BriefItem } from '../api/types'

function Brief() {
  const facts = useBriefingFacts()
  const brief = useBrief()
  const { identity } = useSession()
  const [open, setOpen] = useState<string[] | null>(null)
  const items: BriefItem[] | null = brief.data?.items ?? (facts.data ? [{ title: 'Key facts', sentences: facts.data.facts.slice(0, 4).map((f) => ({ text: f.fact, fact_ids: [f.id] })) }] : null)
  const source = brief.data ? (brief.data.source === 'model' ? `written by ${brief.data.model}, every sentence checked against its facts` : 'deterministic template') : 'deterministic, straight from the facts'
  const evidenceItems = (facts.data?.facts ?? []).map((f) => ({ id: f.id, type: 'p1_metric' as const, label: f.fact, value: '', unit: '', endpoint: `/v1/briefing (${f.metric})`, provenance: 'measured', retrieved_at: facts.data?.as_of }))
  return (
    <Card title="Executive brief" subtitle={<>Every sentence links to the P1 facts it uses · {source}</>}
      actions={<Button onClick={() => brief.mutate()} disabled={!identity || brief.isPending} title={identity ? 'Ask the assistant to write the brief from these facts' : 'Sign in (demo) to generate with the assistant'} data-testid="brief-generate">{brief.isPending ? 'Writing…' : 'Write with Northstar'}</Button>}>
      <Async q={facts} rows={4}>{() => (
        <div className="space-y-3">
          {items?.map((it, i) => (
            <div key={i}>
              <h3 className="text-sm font-semibold">{it.title}</h3>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
                {it.sentences.map((s, j) => <li key={j}>{s.text} <button type="button" className="rounded border border-line px-1 text-[11px] text-accent hover:bg-accent-soft" onClick={() => setOpen(s.fact_ids)} aria-label={`Evidence ${s.fact_ids.join(', ')}`}>{s.fact_ids.join(', ')}</button></li>)}
              </ul>
            </div>
          ))}
          {brief.isError && <ErrorState error={brief.error} />}
          {brief.data?.label && <p className="text-xs text-muted">{brief.data.label}</p>}
        </div>
      )}</Async>
      {open && <EvidenceDrawer items={evidenceItems} highlight={open} onClose={() => setOpen(null)} />}
    </Card>
  )
}

function WhereTime() {
  const flow = useFlow('stage')
  return (
    <Card title="Where the time goes" subtitle="Share of the waiting time of breaching cases spent on each stage transition" actions={<Link to="/process" className="text-sm">Process mining →</Link>}>
      <Async q={flow} rows={4}>{(f) => {
        const top = [...f.edges].sort((a, b) => (b.breach_contribution_pct ?? 0) - (a.breach_contribution_pct ?? 0)).slice(0, 5)
        const label = (id: string) => f.nodes.find((n) => n.id === id)?.label ?? id
        return (
          <ul className="space-y-2">
            {top.map((e) => (
              <li key={`${e.from}>${e.to}`}>
                <div className="flex justify-between text-sm"><span>{label(e.from)} → {label(e.to)}</span><strong>{fmt(e.breach_contribution_pct, 1)}%</strong></div>
                <Bar value={e.breach_contribution_pct ?? 0} max={Math.max(1, top[0]?.breach_contribution_pct ?? 1)} tone="warn" />
                <p className="text-xs text-muted">median wait {fmt(e.median_hours, 0)} h · {fmt(e.transitions)} transitions</p>
              </li>
            ))}
            <li className="pt-1"><ProvBadge p="measured" source={f.note} n={f.closed_cases} /></li>
          </ul>
        )
      }}</Async>
    </Card>
  )
}

export function Overview() {
  const ov = useOverview()
  const queue = useQueue({ limit: 6 })
  const rm = useRiskMap()
  const controls = useFinanceControls()
  const dq = useDataQuality()
  const models = useModels()
  const ints = useInterventions()
  const nav = useNavigate()
  const { identity } = useSession()
  return (
    <div>
      <PageHeader title="Procurement control tower"
        subtitle="Monitor → investigate → prioritise → act → measure. A historical replay of the BPI 2019 log: open cases are the ones still unfinished at the replay clock, scored only from events before it."
        actions={<Link to="/ask" search={{ q: 'Why do cases breach their realistic target, and which stages and suppliers are involved?' }} className="rounded-md border border-accent bg-accent px-3 py-1.5 text-sm font-medium text-accent-ink no-underline">Investigate in Ask Northstar</Link>} />
      <Async q={ov} rows={6} minHeight={620}>{(o) => (
        <div className="space-y-3">
          <p className="text-xs text-muted" data-testid="as-of">Replay clock: <strong>{dateLabel(o.as_of)}</strong></p>
          <Grid cols={4}>
            <Kpi label="Open cases" metric={o.kpis.open_cases} to="/queue" />
            <Kpi label="Already past target" metric={o.kpis.already_late} to="/queue" hint="breach determined by elapsed time" />
            <Kpi label="At risk (experimental)" metric={o.kpis.at_risk_open} to="/queue" hint="weak model signal" />
            <Kpi label="Value on flagged cases" metric={o.kpis.value_flagged_eur} fmtValue={(v) => eur(Number(v))} />
            <Kpi label="Simulated expected loss" metric={o.kpis.expected_loss_eur} fmtValue={(v) => eur(Number(v))} hint="rests on an assumed cost share" />
            <Kpi label="Breach rate · realistic target" metric={o.kpis.breach_rate_realistic_30d} hint="cases whose target window ended in the last 30 days" />
            <Kpi label="AP exceptions" metric={o.kpis.ap_exceptions} to="/finance/controls" hint="anomaly triage, not fraud" />
            <Kpi label="Interventions recorded" metric={o.kpis.interventions_recorded} to="/interventions" />
          </Grid>
          <Link to="/interventions" className="block no-underline text-ink" data-testid="ledger-kpi">
            <div className="rounded-lg border border-line bg-panel p-3 shadow-sm text-sm">
              <span className="text-xs text-muted">Interventions proposed in the assistant's ledger</span>{' '}
              {identity ? (ints.data ? <strong data-testid="ledger-count">{ints.data.interventions.length} proposed · {ints.data.interventions.filter((i) => i.status === 'gateway_held').length} awaiting approval · {ints.data.interventions.filter((i) => i.status === 'executed').length} executed</strong> : <span className="text-muted">loading…</span>) : <span className="text-muted">sign in to see</span>}
              <ProvBadge p="measured" source="assistant intervention ledger (not yet written to P1 unless executed)" />
            </div>
          </Link>
          <p className="text-xs text-muted">Configured-SLA breach rate (secondary, <ProvBadge p={o.kpis.breach_rate_configured_all_closed.provenance} />): <MetricValue m={o.kpis.breach_rate_configured_all_closed} /> — most cases fall back to a 240 h target, so it is not a realistic service level.</p>

          <Grid cols={2}>
            <Brief />
            <Card title="Breach rate by month" subtitle="Realistic target, grouped by the month each case's target window ended">
              {o.trend.series.length ? <TrendChart series={o.trend.series} /> : <p className="text-sm text-muted">No month has 30 decided cases at this clock. Move the replay clock later.</p>}
              <p className="mt-1 text-xs text-muted">{o.trend.note}</p>
            </Card>
          </Grid>

          <Grid cols={3}>
            <Card title="Risk funnel" subtitle="From open cases to recorded interventions">
              <ul className="space-y-2">
                {o.funnel.map((f) => (
                  <li key={f.label}>
                    <div className="flex justify-between text-sm"><span>{f.label}</span><strong className="tabular-nums">{fmt(f.n)}</strong></div>
                    <Bar value={f.n} max={o.funnel[0].n} />
                  </li>
                ))}
              </ul>
            </Card>
            <Card title="Risk × value" subtitle="Click a dot to open the case" className="xl:col-span-2" actions={<Link to="/risk-map" className="text-sm">Full map →</Link>}>
              <Async q={rm} rows={5}>{(r) => <RiskValueScatter points={r.points.map((p) => ({ id: p.case_id, risk: p.p_breach, value: p.value_eur, tier: p.tier, label: `${p.case_id} · ${p.stage}` }))} onOpen={(id) => nav({ to: '/cases/$caseId', params: { caseId: id } })} height={260} />}</Async>
            </Card>
          </Grid>

          <Grid cols={2}>
            <Card title="Action Center — top of the queue" subtitle="Ranked by simulated expected loss" actions={<Link to="/queue" className="text-sm">All cases →</Link>}>
              <Async q={queue} rows={4} isEmpty={(d) => d.rows.length === 0} empty={<p className="text-sm text-muted">No scored open cases at this clock.</p>}>{(qd) => (
                <DataTable caption="Top cases by expected loss" rows={qd.rows} rowKey={(r) => r.case_id} onRowClick={(r) => nav({ to: '/cases/$caseId', params: { caseId: r.case_id } })}
                  columns={[
                    { key: 'c', header: 'Case', render: (r) => <span className="font-mono text-xs">{r.case_id}</span> },
                    { key: 't', header: 'Tier', render: (r) => <TierBadge tier={r.tier} /> },
                    { key: 'v', header: 'Value', align: 'right', render: (r) => eur(r.value_eur) },
                    { key: 'l', header: 'Exp. loss', align: 'right', render: (r) => eur(r.expected_loss_eur) },
                    { key: 's', header: 'Stage', render: (r) => r.stage },
                  ]} />
              )}</Async>
            </Card>
            <WhereTime />
          </Grid>

          <Grid cols={3}>
            <Card title="Control health" actions={<Link to="/finance/controls" className="text-sm">AP controls →</Link>}>
              <Async q={controls} rows={3}>{(c) => (
                <ul className="space-y-1.5 text-sm">
                  {c.controls.map((x) => <li key={x.id} className="flex items-center justify-between gap-2"><span>{x.id.replace(/_/g, ' ')}</span>{x.status === 'not_valid' ? <Badge tone="bad">not valid</Badge> : <Badge tone="good">{x.recall != null ? `recall ${fmt(100 * x.recall, 0)}%` : 'ok'}</Badge>}</li>)}
                  <li className="text-xs text-muted">Recall is against planted synthetic anomalies only.</li>
                </ul>
              )}</Async>
            </Card>
            <Card title="What changed" subtitle="Compared with the previous period at the same clock">
              <p className="text-sm">{o.kpis.breach_rate_realistic_30d.previous?.value != null && o.kpis.breach_rate_realistic_30d.value != null
                ? <>Breach rate on the realistic target moved from <strong>{fmt(Number(o.kpis.breach_rate_realistic_30d.previous.value), 1)}%</strong> to <strong>{fmt(Number(o.kpis.breach_rate_realistic_30d.value), 1)}%</strong> (n = {o.kpis.breach_rate_realistic_30d.previous.n} → {o.kpis.breach_rate_realistic_30d.n}).</>
                : 'Not enough decided cases in both 30-day windows at this clock. Move the replay clock later.'}</p>
              <p className="mt-2 text-xs text-muted">Open-case counts are point-in-time at the clock, so they have no previous period; step the replay clock to compare.</p>
            </Card>
            <Card title="Northstar confidence" subtitle="How much to trust what you see">
              <ul className="space-y-1.5 text-sm">
                <li className="flex justify-between"><span>Data lineage and tests</span><Badge tone="good">good</Badge></li>
                <li className="flex justify-between"><span>Late-stage model validation</span><Badge tone="good">good</Badge></li>
                <li className="flex justify-between"><span>Early-warning signal</span><Badge tone="warn">weak (AUC {models.data ? fmt(models.data.early_warning.per_k['3']?.test_roc_auc, 2) : '…'})</Badge></li>
                <li className="flex justify-between"><span>Explicit SLA coverage</span><Badge tone="warn">{dq.data ? fmt(dq.data.explicit_sla_share_pct, 0) : '…'}%</Badge></li>
                <li className="flex justify-between"><span>Real intervention outcomes</span><Badge tone="bad">none</Badge></li>
              </ul>
              <p className="mt-2 text-xs text-muted">Overall: <strong>moderate</strong>. Interventions are simulated. <Link to="/evidence">Evidence →</Link></p>
            </Card>
          </Grid>
          {!identity && <Callout tone="info" title="Sign in to use the AI features">Pick a demo role in the top bar. Questions, investigations and proposals go through the AI gateway, which needs to know who is asking.</Callout>}
        </div>
      )}</Async>
    </div>
  )
}
