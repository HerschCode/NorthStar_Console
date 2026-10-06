import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page, Route } from '@playwright/test'

const dir = join(fileURLToPath(new URL('.', import.meta.url)), 'fixtures')
const file = (n: string) => JSON.parse(readFileSync(join(dir, `${n}.json`), 'utf-8'))

const live = <T extends object>(d: T): T => ({ ...d, snapshot: { live: true } })

const EVIDENCE = { claims: [{ claim: 'The model beats a simple order-value rule', status: 'not_established', evidence: 'paired CIs include 0' }], experiments: [], limitations: ['Closed historical replay.'] }
const EXPERIMENTS = { experiments: [{ id: 'benford', name: 'Benford first-digit screen', hypothesis: 'x', result: 'failed', decision: 'Not valid', detail: '~55% flags', doc: 'docs/external-validation.md' }], note: '' }

export interface MockState { users: Map<string, { user: string; role: string }>; interventions: any[]; decisions: any[]; nextId: number; nextUser: number; asks: number }
const MOCK_TRACE = 'a'.repeat(32)

/** Recorded P1 data + an in-memory stand-in for the gateway (identity, ask, interventions, approvals), so the demo story runs without services. */
export async function installMocks(page: Page): Promise<MockState> {
  const st: MockState = { users: new Map(), interventions: [], decisions: [], nextId: 1, nextUser: 1, asks: 0 }
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: { 'X-Trace-ID': 'a'.repeat(32) }, body: JSON.stringify(body) })
  const who = (route: Route) => {
    const t = (route.request().headers()['authorization'] ?? '').replace('Bearer ', '')
    return st.users.get(t) ?? null
  }

  await page.route('**/p1/**', async (route) => {
    const u = new URL(route.request().url())
    const path = u.pathname.replace(/^\/p1/, '')
    if (path === '/health') return json(route, { status: 'ok', database_connected: true })
    if (path === '/v1/overview') return json(route, live(file('overview')))
    if (path === '/v1/queue') return json(route, live(file('queue')))
    if (path.startsWith('/v1/cases/')) { const c = file('cases')[decodeURIComponent(path.split('/').pop()!)]; return c ? json(route, live(c)) : json(route, { detail: 'not found' }, 404) }
    if (path === '/v1/suppliers') return json(route, live(file('suppliers')))
    if (path.startsWith('/v1/suppliers/')) { const s = file('supplier_360')[decodeURIComponent(path.split('/').pop()!)]; return s ? json(route, live(s)) : json(route, { detail: 'not found' }, 404) }
    if (path === '/v1/process/flow') return json(route, live(file(u.searchParams.get('group') === 'activity' ? 'process_flow_activity' : 'process_flow_stage')))
    if (path === '/v1/process/variants') return json(route, live(file('process_variants')))
    if (path === '/v1/risk-map') return json(route, live(file('risk_map')))
    if (path === '/v1/finance/controls') return json(route, file('finance_controls'))
    if (path === '/v1/finance/exceptions') return json(route, file('finance_exceptions'))
    if (path === '/v1/finance/working-capital') return json(route, file('finance_working_capital'))
    if (path === '/v1/interventions/roi') return json(route, file('interventions_roi'))
    if (path === '/v1/models') return json(route, file('models'))
    if (path === '/v1/data-quality') return json(route, file('data_quality'))
    if (path === '/v1/lineage') return json(route, file('lineage'))
    if (path === '/v1/experiments') return json(route, EXPERIMENTS)
    if (path === '/v1/evidence') return json(route, EVIDENCE)
    if (path === '/v1/briefing') return json(route, file('briefing'))
    if (path === '/v1/search') return json(route, { results: [] })
    return json(route, { detail: `no fixture for ${path}` }, 404)
  })

  await page.route('**/p3/**', async (route) => {
    const req = route.request()
    const u = new URL(req.url())
    const path = u.pathname.replace(/^\/p3/, '')
    const method = req.method()
    const me = who(route)
    if (path === '/v1/limits') return json(route, { gateway: { per_user_daily: 40, global_daily: 400, used_by_you: st.asks, used_overall: st.asks, resets_in_s: 7200 }, assistant: { mode: 'free-chain', summary: 'some free models are cooling down or have no key', next_available_s: 41, notes: ['Gemini quotas are per project and per model; daily quotas reset at midnight Pacific time.'], models: [
      { provider: 'gemini', model: 'gemini-2.5-flash', state: 'cooling', cooldown_scope: 'minute', retry_after_s: 41, used_minute: 8, used_day: 120, caps: { rpm: null, rpd: null, tpm: null } },
      { provider: 'groq', model: 'openai/gpt-oss-120b', state: 'ok', used_minute: 1, used_day: 14, caps: { rpm: 30, rpd: null, tpm: null } },
      { provider: 'groq', model: 'openai/gpt-oss-20b', state: 'no_key', note: 'set GROQ_API_KEY to enable' }] } })
    if (path === '/v1/services') return json(route, { gateway: { status: 'ok', demo_mode: true }, assistant: { configured: true, reachable: true, detail: 'HTTP 200' } })
    if (path === '/v1/me') return json(route, me ? { authenticated: true, user_id: me.user, role: me.role, source: 'demo', label: 'demo identity' } : { authenticated: false, demo_mode: true, roles: [] })
    if (path === '/v1/demo/login') {
      const { role } = req.postDataJSON()
      const user = `demo-${role}-${st.nextUser++}`
      const token = `mock.${user}`
      st.users.set(token, { user, role })
      return json(route, { token, user_id: user, role, expires_at: Math.floor(Date.now() / 1000) + 3600, label: 'demo identity' })
    }
    if (path === '/v1/ask' && method === 'POST') {
      if (!me) return json(route, { detail: 'authentication required' }, 401)
      st.asks++
      const body = req.postDataJSON()
      return json(route, {
        blocked: false, answer: 'This case is ranked high because it has waited a long time at its current stage.', abstained: false,
        gateway: { decision: 'allow', layers: { rule_based: { decision: 'pass', latency_ms: 0.03 }, scratch_classifier: { decision: 'pass', latency_ms: 0.2 } }, latency_ms: 1.4, trace_id: 'a'.repeat(32) },
        claims: [{ text: 'The case has been idle for a long time.', supported: true, evidence_ids: ['e2'], reason: 'figures match' }, { text: 'About 900 cases are similar.', supported: false, evidence_ids: ['e3'], reason: 'figure(s) not found in the cited evidence' }],
        evidence: [{ id: 'e2', type: 'p1_metric', endpoint: `/v1/cases/${body.context?.case_id ?? ''}`, label: 'risk.idle_hours', value: 1200, unit: 'h', provenance: 'descriptive', retrieved_at: '2026-10-05T00:00:00Z' }, { id: 'e3', type: 'policy', doc_id: 'policy-1', title: 'Procurement Policy', section: '4.2', version: null, citation: 'Procurement Policy, Section 4.2', excerpt: 'Orders above 10,000 euros require a second approval.' }],
        model: 'template-fallback', trace_id: 'a'.repeat(32), cost_usd: 0, latency_ms: 12, supported_claims: 1, total_claims: 2,
      })
    }
    if (path === '/v1/interventions' && method === 'POST') {
      if (!me) return json(route, { detail: 'authentication required' }, 401)
      const b = req.postDataJSON()
      const row = { id: st.nextId++, case_id: b.case_id, intervention_type: b.intervention_type, rationale: b.rationale, risk: b.risk, proposer: me.user, proposer_role: me.role, status: 'gateway_held', approval_id: `apr_${st.nextId}`,
        gateway_decision: { effect: 'require_approval', stage: 'approval', reasons: ['write action requires human approval'], risk: 'medium' }, p1_intervention_id: null, assignment: null, outcome: null, created: Date.now() / 1000, updated: Date.now() / 1000,
        history: [{ ts: Date.now() / 1000, status: 'proposed', actor: me.user, detail: { trace_id: MOCK_TRACE } }, { ts: Date.now() / 1000, status: 'gateway_held', actor: 'gateway', detail: { trace_id: MOCK_TRACE } }] }
      st.interventions.unshift(row)
      return json(route, row, 201)
    }
    if (path === '/v1/interventions' && method === 'GET') return json(route, { interventions: me ? st.interventions : [] })
    const m = path.match(/^\/v1\/interventions\/(\d+)\/(approve|reject|outcome)$/)
    if (m && method === 'POST') {
      const row = st.interventions.find((r) => r.id === Number(m[1]))
      if (!me || !row) return json(route, { detail: 'not found' }, 404)
      if (me.user === row.proposer) return json(route, { detail: 'separation of duties: the proposer cannot decide their own intervention' }, 403)
      if (!['manager', 'admin'].includes(me.role)) return json(route, { detail: `role '${me.role}' may not decide interventions` }, 403)
      if (m[2] === 'approve') { row.status = 'executed'; row.p1_intervention_id = 7; row.assignment = 'treat'; row.history.push({ ts: Date.now() / 1000, status: 'approved', actor: me.user, detail: { trace_id: MOCK_TRACE } }, { ts: Date.now() / 1000, status: 'executed', actor: 'system', detail: { trace_id: MOCK_TRACE } }); st.decisions.push({ kind: 'action', ts: Date.now() / 1000, tool: 'propose_intervention', effect: 'approval_granted', stage: 'approval_decision', rule: null, risk: 'medium', role: me.role, session: 'abc123def0', trace_id: MOCK_TRACE, approval_id: row.approval_id }) }
      if (m[2] === 'reject') { row.status = 'rejected'; row.history.push({ ts: Date.now() / 1000, status: 'rejected', actor: me.user, detail: { trace_id: MOCK_TRACE } }); st.decisions.push({ kind: 'action', ts: Date.now() / 1000, tool: 'propose_intervention', effect: 'approval_rejected', stage: 'approval_decision', rule: null, risk: 'medium', role: me.role, session: 'abc123def0', trace_id: MOCK_TRACE, approval_id: row.approval_id }) }
      return json(route, row)
    }
    if (path === '/v1/governance/summary') return json(route, { window: '1h', requests: st.asks, blocked_requests: 0, block_rate: st.asks ? 0 : null, blocks_by_layer: {}, blocks_by_rule: {}, pii_requests: 0, latency_ms: { p50: 1.4, p95: 2.1 }, actions: { total: st.interventions.length, held: st.interventions.length }, series: [] })
    if (path === '/v1/governance/events') return json(route, { detail_level: 'redacted', note: 'Session ids are hashed and reasons are withheld below the admin role.', events: [...st.interventions.map((r) => ({ kind: 'action', ts: r.created, tool: 'propose_intervention', effect: 'require_approval', stage: 'approval', rule: 'manager-standard', risk: 'medium', role: r.proposer_role, session: 'abc123def0', trace_id: MOCK_TRACE, approval_id: r.approval_id })), ...st.decisions] })
    if (path === '/v1/governance/policy-matrix') return json(route, { roles: ['employee', 'analyst', 'manager', 'admin'], default_effect: 'deny', policy_sha256: 'deadbeefdeadbeef', tools: [{ tool: 'propose_intervention', kind: 'write', output_trust: 'trusted', taint: { target: 'deny' }, max_per_session: 5, cells: { employee: { effect: 'deny', rules: [] }, analyst: { effect: 'approval', rules: [{ name: 'manager-standard', approval: 'required', require_role_separation: false, args: {} }] }, manager: { effect: 'approval', rules: [] }, admin: { effect: 'approval', rules: [] } } }] })
    if (path === '/v1/rules') return json(route, { rules: [], note: '' })
    if (path === '/v1/config') return json(route, { detector_backend: { classifier: 'numpy', embedding: 'none', lite_mode: false }, thresholds: { classifier: 0.5 }, policy: { sha256: 'deadbeefdeadbeef', default_effect: 'deny' }, model_integrity: { mode: 'off', artifact_hashes: {} }, demo_mode: true, assistant_configured: true })
    if (path === '/v1/lab/scenarios') return json(route, { scenarios: [{ id: 'TX-1', category: 'prompt_injection', title: 'Direct instruction override', kind: 'text', curated: true, harmful: true }], note: '' })
    if (path === '/v1/approvals') return json(route, { approvals: [] })
    if (path === '/v1/investigations' && method === 'GET') return json(route, { investigations: [] })
    return json(route, { detail: `no mock for ${method} ${path}` }, 404)
  })
  return st
}
