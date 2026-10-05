import { useState } from 'react'
import { Link, useSearch } from '@tanstack/react-router'
import { useInvestigate, useInvestigations } from '../api/hooks'
import { Async, Badge, Button, Callout, Card, ErrorState, Grid, PageHeader } from '../components/ui'
import { AskPanel, ClaimList, EvidenceDrawer, GatewayChip } from '../components/domain'
import { getToken, P3_BASE } from '../api/client'
import { useSession } from '../state/session'
import type { Investigation } from '../api/types'

export function Ask() {
  const search = (useSearch({ strict: false }) as { q?: string })
  const { identity } = useSession()
  return (
    <div>
      <PageHeader title="Ask Northstar" subtitle="Answers come only from numbered evidence (live P1 data and policy documents). Every claim is checked against the evidence it cites; unsupported claims are marked, not hidden." />
      {!identity && <Callout tone="info" title="Sign in to ask">Questions travel through the AI gateway, which needs an identity. Use “Sign in (demo)” in the top bar.</Callout>}
      <Card className="mt-3"><AskPanel key={search.q ?? 'blank'} context={{ page: 'overview' }} initial={search.q} autoRun={!!search.q && !!identity}
        suggestions={['How many open cases are already late?', 'Which stage holds the most open cases?', 'What does the data not tell us yet?', 'Which AP controls are operationally valid?', 'What does policy say about duplicate invoices?']} /></Card>
    </div>
  )
}

async function download(id: string, format: 'md' | 'pdf') {
  const r = await fetch(`${P3_BASE}/v1/investigations/${id}/export?format=${format}`, { headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {} })
  if (!r.ok) throw new Error(`export failed (${r.status})`)
  const blob = await r.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `investigation-${id.slice(0, 8)}.${format}`
  a.click()
  URL.revokeObjectURL(a.href)
}

function Workspace({ inv }: { inv: Investigation }) {
  const [drawer, setDrawer] = useState<string[] | null>(null)
  const all = [...inv.evidence.data, ...inv.evidence.documents]
  return (
    <div className="space-y-3" data-testid="investigation">
      <GatewayChip g={inv.gateway} />
      {inv.blocked ? <Callout tone="bad" title="Stopped by the AI gateway">{inv.gateway?.reason}</Callout> : (
        <>
          <Card title="Summary" actions={<div className="flex gap-2"><Button onClick={() => download(inv.id, 'md').catch(() => undefined)}>Export Markdown</Button><Button onClick={() => download(inv.id, 'pdf').catch(() => undefined)}>Export PDF</Button></div>}>
            <p className="text-sm">{inv.summary}</p>
            <p className="mt-1 text-xs text-muted">Model {inv.model} · trace {inv.trace_id.slice(0, 8)} · <button type="button" className="underline" onClick={() => setDrawer([])}>all evidence ({all.length})</button></p>
          </Card>
          {inv.warnings.length > 0 && <Callout tone="warn" title="Wording flagged">{inv.warnings.join(' ')}</Callout>}
          <Grid cols={2}>
            <Card title="Root causes (association, not causation)">{inv.root_causes.length ? <ClaimList claims={inv.root_causes} onEvidence={setDrawer} /> : <p className="text-sm text-muted">None generated (no model, or no evidence).</p>}</Card>
            <Card title="Recommendations">{inv.recommendations.length ? <ul className="list-disc space-y-1 pl-5 text-sm">{inv.recommendations.map((r, i) => <li key={i} className={r.supported === false ? 'text-bad' : ''}>{r.text}{r.supported === false && <span className="block text-xs text-muted">not verified</span>}</li>)}</ul> : <p className="text-sm text-muted">None generated.</p>}</Card>
          </Grid>
          <Grid cols={2}>
            <Card title="Evidence — live data">{inv.evidence.data.length ? <ul className="space-y-1 text-sm">{inv.evidence.data.slice(0, 12).map((e) => <li key={e.id}><span className="font-mono text-xs">{e.id}</span> {e.label} = <strong>{String(e.value)}</strong> {e.unit}</li>)}</ul> : <p className="text-sm text-muted">None.</p>}</Card>
            <Card title="Evidence — documents & policy">{inv.relevant_policy.length ? <ul className="list-disc pl-5 text-sm">{inv.relevant_policy.map((p, i) => <li key={i}>{p}</li>)}</ul> : <p className="text-sm text-muted">No policy text was retrieved.</p>}</Card>
          </Grid>
          <Card title="Limitations"><p className="text-sm">{inv.limitations || 'None stated.'}</p></Card>
        </>
      )}
      {drawer && <EvidenceDrawer items={all} highlight={drawer} onClose={() => setDrawer(null)} />}
    </div>
  )
}

export function Investigations() {
  const search = (useSearch({ strict: false }) as { q?: string })
  const { identity } = useSession()
  const run = useInvestigate()
  const list = useInvestigations()
  const [q, setQ] = useState(search.q ?? '')
  const [open, setOpen] = useState<Investigation | null>(null)
  const start = async () => { setOpen(null); const r = await run.mutateAsync({ question: q, context: { page: 'overview' } }); setOpen(r) }
  return (
    <div>
      <PageHeader title="Investigations" subtitle="A fixed-shape report: summary, evidence (data vs documents), root causes in association language, policy, recommendations, limitations. Saved and exportable." />
      <Card>
        <form onSubmit={(e) => { e.preventDefault(); if (q.trim().length > 2) start().catch(() => undefined) }} className="flex gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Investigation question" placeholder="What should be investigated?" className="min-w-0 flex-1 rounded-md border border-line bg-panel2 px-3 py-2" data-testid="inv-input" />
          <Button variant="primary" type="submit" disabled={!identity || run.isPending || q.trim().length < 3} data-testid="inv-run">{run.isPending ? 'Investigating…' : 'Investigate'}</Button>
        </form>
        {!identity && <p className="mt-2 text-xs text-muted">Sign in (demo) to run an investigation.</p>}
        {run.isError && <div className="mt-2"><ErrorState error={run.error} /></div>}
      </Card>
      <div className="mt-3 space-y-3">
        {open && <Workspace inv={open} />}
        <Card title="Saved investigations">
          <Async q={list} rows={3} isEmpty={(d) => d.investigations.length === 0} empty={<p className="text-sm text-muted">{identity ? 'None yet.' : 'Sign in to see saved investigations.'}</p>}>{(d) => (
            <ul className="divide-y divide-line">{d.investigations.map((i) => (
              <li key={i.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="min-w-0"><span className="font-medium">{i.question}</span><span className="block truncate text-xs text-muted">{i.summary}</span></span>
                <span className="flex shrink-0 items-center gap-2"><Badge>{i.model}</Badge><Link to="/investigations" search={{ q: i.question }}>re-run</Link></span>
              </li>))}</ul>
          )}</Async>
        </Card>
      </div>
    </div>
  )
}
