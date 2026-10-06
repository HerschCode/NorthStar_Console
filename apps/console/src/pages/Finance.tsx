import { useState } from 'react'
import { useFinanceControls, useFinanceExceptions, useWorkingCapital } from '../api/hooks'
import { Async, Badge, Bar, Callout, Card, DataTable, Grid, PageHeader } from '../components/ui'
import { MiniStat } from '../components/domain'
import { ProvBadge } from '../components/Prov'
import { eur, fmt, pct } from '../lib/format'

const SEV_TONE: Record<string, 'bad' | 'warn' | 'neutral'> = { high: 'bad', medium: 'warn', low: 'neutral' }

export function ApControls() {
  const controls = useFinanceControls()
  const exc = useFinanceExceptions()
  return (
    <div>
      <PageHeader title="AP controls" subtitle="Six rule-based controls over the real event log. They flag anomalies for triage; they are not fraud findings." />
      <Async q={controls} rows={5}>{(c) => (
        <Grid cols={1}>
          <Grid cols={3}>
            {c.controls.map((x) => (
              <Card key={x.id} title={x.id.replace(/_/g, ' ')} actions={x.status === 'not_valid' ? <Badge tone="bad">not operationally valid</Badge> : <Badge tone="good">operational</Badge>}>
                {x.status === 'not_valid' ? (
                  <p className="text-sm">{x.note}</p>
                ) : (
                  <div className="space-y-1.5">
                    <MiniStat label="Recall on planted anomalies">{pct(100 * (x.recall ?? 0), 1)} <span className="text-xs text-muted">(95% CI {fmt(100 * (x.recall_ci95?.[0] ?? 0), 0)}–{fmt(100 * (x.recall_ci95?.[1] ?? 0), 0)}%)</span></MiniStat>
                    <Bar value={100 * (x.recall ?? 0)} tone="good" />
                    <p className="text-xs text-muted">False-positive rate on real data ≤ {pct(100 * (x.false_positive_rate_upper_bound ?? 0), 1)} (upper bound: no ground truth).</p>
                  </div>
                )}
                <div className="mt-2"><ProvBadge p="measured" source="planted-anomaly evaluation (synthetic injections)" /></div>
              </Card>
            ))}
          </Grid>
          <Callout tone="warn" title="Benford screen: do not use for decisions">On external data (Online Retail II and SEC filings) it flags ~55% of ordinary customers and filings against a 5% null, so a flag carries almost no information. It is shown, not hidden.</Callout>
          {c.external_c4 && <Card title="External validation — duplicate-invoice control" actions={<ProvBadge p="measured" source="UCI Online Retail II (a sales ledger standing in for payables)" />}>
            <p className="text-sm">{fmt(c.external_c4.flagged_invoices)} invoices flagged; <strong>{pct(100 * c.external_c4.precision_vs_reversal_proxy, 1)}</strong> were later reversed by a credit note — a <strong>{fmt(c.external_c4.lift_over_base, 0)}×</strong> lift over the base rate. {c.external_c4.note}.</p>
          </Card>}
        </Grid>
      )}</Async>
      <h2 className="mb-2 mt-5 text-lg font-semibold">Exception matrix</h2>
      <Async q={exc} rows={4} isEmpty={(d) => d.summary.length === 0} empty={<Callout tone="warn" title="AP controls have not been run">Run <code>scripts/run_ap_controls.py</code> in P1.</Callout>}>{(e) => (
        <Card title={`${fmt(e.total)} exceptions on the real log`} actions={<ProvBadge p="measured" source="AP controls over the whole log (not clocked)" />} subtitle={`Exposure p50 ${eur(e.exposure_cuts_eur.p50)}, p90 ${eur(e.exposure_cuts_eur.p90)}`}>
          <DataTable caption="Exceptions by control and severity" rows={e.summary} rowKey={(r) => r.control_id + r.severity} columns={[
            { key: 'c', header: 'Control', render: (r) => r.control_id.replace(/_/g, ' ') }, { key: 's', header: 'Severity', render: (r) => <Badge tone={SEV_TONE[r.severity] ?? 'neutral'}>{r.severity}</Badge> },
            { key: 'n', header: 'Count', align: 'right', render: (r) => fmt(r.count) }, { key: 'x', header: 'Exposure', align: 'right', render: (r) => eur(r.exposure_eur) }]} />
        </Card>
      )}</Async>
    </div>
  )
}

export function WorkingCapital() {
  const q = useWorkingCapital()
  const [disc, setDisc] = useState(2)
  return (
    <div>
      <PageHeader title="Working capital" subtitle="Late-payment exposure and an early-payment discount what-if. The log has no payment terms, so net-30 is an assumption." />
      <Async q={q} rows={5}>{(w) => {
        const baseDisc = 100 * w.early_discount_scenario.discount_pct
        const potential = w.early_discount_scenario.potential_discount_eur * (disc / baseDisc)
        const max = Math.max(...w.dpo_by_spend_area.map((r) => r.median_days), 1)
        return (
          <Grid cols={1}>
            <Grid cols={3}>
              <Card><MiniStat label="Late-payment exposure">{eur(w.late_payment.exposure_eur)} <ProvBadge p="simulated" source={w.source} /></MiniStat><p className="mt-1 text-xs text-muted">Value of {fmt(w.late_payment.n_late)} of {fmt(w.late_payment.n_cases)} invoices paid after {w.late_payment.payment_terms_days} days (assumed terms).</p></Card>
              <Card><MiniStat label="Invoices paid late">{pct(w.late_payment.late_rate_pct, 1)} <ProvBadge p="simulated" source={w.source} /></MiniStat><p className="mt-1 text-xs text-muted">High because the terms are an assumption, not data.</p></Card>
              <Card><MiniStat label={`Discount missed at ${fmt(disc, 1)}%`}>{eur(w.early_discount_scenario.missed_discount_eur * (disc / baseDisc))} <ProvBadge p="simulated" source="what-if" /></MiniStat><p className="mt-1 text-xs text-muted">{fmt(w.early_discount_scenario.n_captured)} invoices were paid inside {w.early_discount_scenario.discount_window_days} days.</p></Card>
            </Grid>
            <Card title="Early-payment discount scenario" subtitle="Assumption: discount rate (the window stays at the scenario's value). Scales linearly from the computed scenario.">
              <label className="block text-sm" htmlFor="disc">Discount rate: <strong>{fmt(disc, 1)}%</strong> <span className="text-xs text-muted">(assumed)</span></label>
              <input id="disc" type="range" min={0.5} max={5} step={0.5} value={disc} onChange={(e) => setDisc(Number(e.target.value))} className="mt-1 w-full max-w-md" data-testid="discount-slider" />
              <p className="mt-2 text-sm">Potential discount if every eligible invoice were paid within {w.early_discount_scenario.discount_window_days} days: <strong>{eur(potential)}</strong> across {fmt(w.early_discount_scenario.n_eligible)} invoices.</p>
            </Card>
            <Card title="Median days to pay by spend area" actions={<ProvBadge p="measured" source={w.source} />}>
              <ul className="space-y-1.5">{w.dpo_by_spend_area.map((r) => <li key={r.sub_spend_area}><div className="flex justify-between text-sm"><span>{r.sub_spend_area} <span className="text-xs text-muted">({r.case_count})</span></span><strong>{fmt(r.median_days, 0)} d</strong></div><Bar value={r.median_days} max={max} /></li>)}</ul>
            </Card>
          </Grid>
        )
      }}</Async>
    </div>
  )
}
