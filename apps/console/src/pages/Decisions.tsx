import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useDecideIntervention, useInterventions, useRoi } from '../api/hooks'
import { Async, Badge, Button, Callout, Card, DataTable, Grid, PageHeader, ErrorState } from '../components/ui'
import { StatusBadge } from '../components/domain'
import { ProvBadge } from '../components/Prov'
import { GroupedBars } from '../components/LazyCharts'
import { eur, fmt } from '../lib/format'
import { useSession } from '../state/session'
import type { InterventionRow } from '../api/types'

function Review({ row, onClose }: { row: InterventionRow; onClose: () => void }) {
  const { identity } = useSession()
  const roi = useRoi()
  const decide = useDecideIntervention()
  const a = roi.data?.simulated.assumptions
  const t = a?.types[row.intervention_type]
  const net = t && a ? t.effect * a.breach_cost - t.cost : null
  const isProposer = identity?.userId === row.proposer
  const canDecide = !!identity && ['manager', 'admin'].includes(identity.role) && !isProposer && row.status === 'gateway_held'
  const why = !identity ? 'Sign in as a manager or admin to decide.' : isProposer ? 'Separation of duties: the proposer cannot decide their own request.' : !['manager', 'admin'].includes(identity.role) ? `Role “${identity.role}” may not decide approvals.` : row.status !== 'gateway_held' ? `Status is “${row.status}”.` : ''
  const [breached, setBreached] = useState(false)
  return (
    <Card title={`Intervention #${row.id} — case ${row.case_id}`} actions={<Button variant="ghost" onClick={onClose} aria-label="Close review">✕</Button>}>
      <div className="flex flex-wrap items-center gap-2"><StatusBadge status={row.status} /><span className="text-sm">{row.intervention_type.replace(/_/g, ' ')} · proposed by {row.proposer} ({row.proposer_role})</span></div>
      <p className="mt-2 text-sm">{row.rationale}</p>
      <Grid cols={2} className="mt-3">
        <div className="rounded-md border border-line p-3 text-sm">
          <div className="font-semibold">AI gateway security checks</div>
          <ul className="mt-1 list-disc pl-5 text-muted">
            <li>Decision: {row.gateway_decision?.effect ?? '—'}{row.gateway_decision?.stage ? ` at the ${row.gateway_decision.stage} stage` : ''}</li>
            <li>Risk rating: {row.gateway_decision?.risk ?? '—'}</li>
            {row.gateway_decision?.reasons?.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
        <div className="rounded-md border border-line p-3 text-sm">
          <div className="flex items-center gap-2 font-semibold">Simulated value <ProvBadge p="simulated" source="config/interventions.yaml" /></div>
          {t && a ? <p className="mt-1 text-muted">Assumed cost {eur(t.cost)}, assumed {fmt(100 * t.effect, 0)}% cut in breach probability, avoided-breach value {eur(a.breach_cost)}: net <strong className={(net ?? 0) >= 0 ? 'text-good' : 'text-bad'}>{eur(net)}</strong>. Not a measured effect.</p> : <p className="text-muted">Assumptions unavailable.</p>}
        </div>
      </Grid>
      <h3 className="mb-1 mt-3 text-sm font-semibold">Status history</h3>
      <ol className="space-y-1 border-l-2 border-line pl-3 text-sm" aria-label="Status history">
        {row.history.map((h, i) => <li key={i}><StatusBadge status={h.status} /> <span className="text-xs text-muted">{new Date(h.ts * 1000).toLocaleString()} · {h.actor}</span></li>)}
      </ol>
      {row.status === 'gateway_held' && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="primary" disabled={!canDecide || decide.isPending} onClick={() => decide.mutate({ id: row.id, verb: 'approve', body: { note: 'approved in console' } })} data-testid="approve">Approve</Button>
          <Button variant="danger" disabled={!canDecide || decide.isPending} onClick={() => decide.mutate({ id: row.id, verb: 'reject', body: { note: 'rejected in console' } })} data-testid="reject">Reject</Button>
          {!canDecide && <span className="text-xs text-muted" data-testid="why-disabled">{why}</span>}
        </div>
      )}
      {row.status === 'executed' && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <span>{row.assignment === 'holdout' ? 'Holdout case: logged for its outcome, no action was taken.' : 'Treated.'} Record the outcome:</span>
          <label className="flex items-center gap-1"><input type="checkbox" checked={breached} onChange={(e) => setBreached(e.target.checked)} /> case breached</label>
          <Button onClick={() => decide.mutate({ id: row.id, verb: 'outcome', body: { breached_after: breached } })} disabled={decide.isPending}>Record outcome</Button>
        </div>
      )}
      {row.outcome && <p className="mt-2 text-sm">Outcome recorded: <strong>{row.outcome.replace('_', ' ')}</strong>. <ProvBadge p="measured" source="analytics.interventions ledger" /></p>}
      {decide.isError && <div className="mt-2"><ErrorState error={decide.error} /></div>}
    </Card>
  )
}

export function Interventions() {
  const q = useInterventions()
  const { identity } = useSession()
  const [sel, setSel] = useState<number | null>(null)
  const rows = q.data?.interventions ?? []
  const selected = rows.find((r) => r.id === sel) ?? null
  const counts = (s: string) => rows.filter((r) => r.status === s).length
  return (
    <div>
      <PageHeader title="Interventions" subtitle="Proposal → AI-gateway checks → human approval (a different person; a different role for payment release) → execution → outcome. Effects are assumptions until real outcomes exist." />
      {!identity && <Callout tone="info" title="Sign in to see interventions">The ledger is behind the AI gateway. Use “Sign in (demo)” in the top bar.</Callout>}
      <Grid cols={4}>
        <Card><div className="text-xs text-muted">Held for approval</div><div className="text-2xl font-semibold" data-testid="count-held">{counts('gateway_held')}</div></Card>
        <Card><div className="text-xs text-muted">Executed</div><div className="text-2xl font-semibold" data-testid="count-executed">{counts('executed')}</div></Card>
        <Card><div className="text-xs text-muted">Denied by the gateway</div><div className="text-2xl font-semibold">{counts('gateway_denied')}</div></Card>
        <Card><div className="text-xs text-muted">With a recorded outcome</div><div className="text-2xl font-semibold">{rows.filter((r) => r.outcome).length}</div></Card>
      </Grid>
      <Grid cols={1} className="mt-3">
        {selected && <Review row={selected} onClose={() => setSel(null)} />}
        <Card title="Interventions" subtitle="Newest first. Propose from a case page or an assistant answer.">
          <Async q={q} rows={4} isEmpty={(d) => d.interventions.length === 0} empty={<p className="text-sm text-muted">No interventions yet. Open a case in the <Link to="/queue">Action Center</Link> and choose “Propose intervention”.</p>}>{(d) => (
            <DataTable caption="Interventions" rows={d.interventions} rowKey={(r) => String(r.id)} onRowClick={(r) => setSel(r.id)} columns={[
              { key: 'id', header: '#', render: (r) => r.id }, { key: 'c', header: 'Case', render: (r) => <Link to="/cases/$caseId" params={{ caseId: r.case_id }} onClick={(e) => e.stopPropagation()}>{r.case_id}</Link> },
              { key: 't', header: 'Type', render: (r) => r.intervention_type.replace(/_/g, ' ') }, { key: 's', header: 'Status', render: (r) => <StatusBadge status={r.status} /> },
              { key: 'p', header: 'Proposed by', render: (r) => `${r.proposer} (${r.proposer_role})` }, { key: 'o', header: 'Outcome', render: (r) => r.outcome?.replace('_', ' ') ?? '—' }]} />
          )}</Async>
        </Card>
      </Grid>
    </div>
  )
}

export function Roi() {
  const q = useRoi()
  return (
    <div>
      <PageHeader title="ROI & uplift" subtitle="Simulated and logged results are never mixed. No real intervention outcomes exist yet, so every value below is a simulation or an offline comparison." />
      <Async q={q} rows={5}>{(r) => {
        const pk = r.model_vs_rules?.precision_at_k ?? {}
        const shares = ['5%', '10%', '20%', '30%']
        const pick = (names: string[]) => names.map((n) => Object.keys(pk).find((k) => k === n || k.startsWith(n))).find(Boolean)
        const series = [['Model (as deployed)', pick(['M0_current']), 'accent'], ['Order-value rule', pick(['order_value']), 'warn'], ['Random', pick(['random']), 'muted']] as const
        return (
          <Grid cols={1}>
            <Card title="Logged outcomes" actions={<ProvBadge p="pending" source="analytics.interventions ledger" />}><p className="text-sm">{String(r.logged.value)} interventions with recorded outcomes. {r.logged.note}</p></Card>
            {r.model_vs_rules && (
              <Card title="Model vs rules vs random — precision at the treated share" actions={<ProvBadge p="measured" source="PO-isolated holdout" n={r.model_vs_rules.n_test} />} subtitle={r.model_vs_rules.verdict}>
                <GroupedBars categories={shares} label="Precision at 5, 10, 20 and 30 percent treated share for the model, the order-value rule and random" height={280}
                  groups={series.filter((s) => s[1]).map(([name, key, tone]) => ({ name, tone, data: shares.map((s) => pk[key as string]?.[s]?.precision ?? null) }))} />
                <p className="mt-1 text-xs text-muted">Confidence intervals are wide (held-out cases cluster by purchase order); see <code>reports/model_vs_rules.json</code>. The model's advantage over the order-value rule is not established.</p>
              </Card>
            )}
            <Card title="Simulation assumptions" actions={<ProvBadge p="simulated" source="config/interventions.yaml" />}>
              <DataTable caption="Assumed intervention types" rows={Object.entries(r.simulated.assumptions.types)} rowKey={([k]) => k} columns={[
                { key: 'k', header: 'Type', render: ([k]) => k.replace(/_/g, ' ') }, { key: 'c', header: 'Assumed cost', align: 'right', render: ([, v]) => eur(v.cost) }, { key: 'e', header: 'Assumed effect', align: 'right', render: ([, v]) => `${fmt(100 * v.effect, 0)}%` }]} />
              <p className="mt-2 text-xs text-muted">Avoided-breach value {eur(r.simulated.assumptions.breach_cost)}; capacity {fmt(100 * r.simulated.assumptions.capacity_pct, 0)}% of cases. These are assumptions a business owner would replace.</p>
            </Card>
            <Callout tone="info" title="How measurement will work">Proposals are randomized into treat vs holdout; recording outcomes on the holdout is what turns the assumed effects into measured uplift (T/X-learner, Qini). Until then ROI stays simulated.</Callout>
          </Grid>
        )
      }}</Async>
    </div>
  )
}
export { Badge }
